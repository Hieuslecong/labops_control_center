package repositoryguard

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

type GuardService struct {
	dataDir       string
	quarantineDir string
	store         *ApprovedArtifactStore
	audit         *AuditLogger
	requestsPath  string
	mu            sync.RWMutex
	requests      map[string]*RepositoryRequest
}

func NewGuardService(dataDir string) (*GuardService, error) {
	if err := os.MkdirAll(dataDir, 0750); err != nil {
		return nil, fmt.Errorf("không thể tạo dataDir: %w", err)
	}

	quarantineDir := filepath.Join(dataDir, "quarantine")
	_ = os.MkdirAll(quarantineDir, 0750)

	storeDir := filepath.Join(dataDir, "approved_store")
	store, err := NewApprovedArtifactStore(storeDir)
	if err != nil {
		return nil, err
	}

	auditPath := filepath.Join(dataDir, "audit_events.jsonl")
	audit, err := NewAuditLogger(auditPath)
	if err != nil {
		return nil, err
	}

	svc := &GuardService{
		dataDir:       dataDir,
		quarantineDir: quarantineDir,
		store:         store,
		audit:         audit,
		requestsPath:  filepath.Join(dataDir, "requests.json"),
		requests:      make(map[string]*RepositoryRequest),
	}

	svc.loadRequests()
	svc.recoverStaleJobs()

	return svc, nil
}

func (s *GuardService) generateID() string {
	b := make([]byte, 6)
	_, _ = rand.Read(b)
	return fmt.Sprintf("req-%s", hex.EncodeToString(b))
}

func (s *GuardService) loadRequests() {
	s.mu.Lock()
	defer s.mu.Unlock()

	data, err := os.ReadFile(s.requestsPath)
	if err != nil {
		return
	}

	var list []*RepositoryRequest
	if err := json.Unmarshal(data, &list); err == nil {
		for _, req := range list {
			s.requests[req.ID] = req
		}
	}
}

func (s *GuardService) saveRequests() error {
	var list []*RepositoryRequest
	for _, req := range s.requests {
		list = append(list, req)
	}

	data, err := json.MarshalIndent(list, "", "  ")
	if err != nil {
		return err
	}

	tmp := s.requestsPath + ".tmp"
	if err := os.WriteFile(tmp, data, 0640); err != nil {
		return err
	}
	return os.Rename(tmp, s.requestsPath)
}

func (s *GuardService) recoverStaleJobs() {
	s.mu.Lock()
	defer s.mu.Unlock()

	modified := false
	for _, req := range s.requests {
		if req.State == StateSubmitted || req.State == StateValidating ||
			req.State == StateResolvingCommit || req.State == StateCheckingCache ||
			req.State == StateFetching || req.State == StateScanning ||
			req.State == StatePolicyEvaluation {
			req.State = StateFailed
			req.Error = "PROCESS_INTERRUPTED: Tiến trình bị gián đoạn do khởi động lại dịch vụ (Durable Queue Recovery)"
			req.UpdatedAt = time.Now()
			modified = true
		}
	}
	if modified {
		_ = s.saveRequests()
	}
}

// SubmitRequest tiếp nhận yêu cầu clone repo từ Researcher hoặc Admin
func (s *GuardService) SubmitRequest(ctx context.Context, rawURL, ref, submitter, targetWorkspace string) (*RepositoryRequest, error) {
	canonicalURL, owner, repo, err := NormalizeAndValidateURL(rawURL)
	if err != nil {
		return nil, fmt.Errorf("URL không hợp lệ: %w", err)
	}

	reqID := s.generateID()
	now := time.Now()

	req := &RepositoryRequest{
		ID:              reqID,
		RepoURL:         rawURL,
		CanonicalRepo:   canonicalURL,
		Owner:           owner,
		RepoName:        repo,
		RequestedRef:    ref,
		Submitter:       submitter,
		TargetWorkspace: targetWorkspace,
		SubmittedAt:     now,
		UpdatedAt:       now,
		State:           StateSubmitted,
		PolicyVersion:   CurrentPolicyVersion,
	}

	s.mu.Lock()
	s.requests[reqID] = req
	_ = s.saveRequests()
	s.mu.Unlock()

	s.audit.LogEvent(submitter, "REPO_REQUEST_SUBMITTED", "REPOSITORY", canonicalURL, "SUCCESS",
		fmt.Sprintf("Yêu cầu kiểm duyệt repo %s (ref: %s)", canonicalURL, ref))

	// Chạy quy trình kiểm duyệt (xử lý bất đồng bộ nhưng durable)
	go s.processRequest(context.Background(), reqID)

	return req, nil
}

func (s *GuardService) updateRequest(reqID string, fn func(r *RepositoryRequest)) *RepositoryRequest {
	s.mu.Lock()
	defer s.mu.Unlock()

	req, ok := s.requests[reqID]
	if !ok {
		return nil
	}
	fn(req)
	req.UpdatedAt = time.Now()
	_ = s.saveRequests()
	return req
}

func (s *GuardService) processRequest(ctx context.Context, reqID string) {
	// 1. Resolving commit SHA
	s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StateResolvingCommit })

	s.mu.RLock()
	req := s.requests[reqID]
	s.mu.RUnlock()
	if req == nil {
		return
	}

	commitSHA, err := ResolveCommitSHA(ctx, req.CanonicalRepo, req.RequestedRef)
	if err != nil {
		s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateFailed
			r.Error = fmt.Sprintf("Lỗi phân giải commit SHA: %v", err)
		})
		s.audit.LogEvent("SYSTEM", "COMMIT_RESOLUTION_FAILED", "REPOSITORY", req.CanonicalRepo, "FAILED", err.Error())
		return
	}

	s.updateRequest(reqID, func(r *RepositoryRequest) {
		r.CommitSHA = commitSHA
		r.State = StateCheckingCache
	})

	// 2. Kiểm tra Approved Cache
	if entry, hit := s.store.GetApproved(req.CanonicalRepo, commitSHA); hit {
		s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateAutoApproved
			r.CacheHit = true
			r.ArtifactDigest = entry.ArtifactDigest
			r.ArtifactSize = entry.ArtifactSize
		})
		s.audit.LogEvent("SYSTEM", "CACHE_HIT", "COMMIT", commitSHA, "SUCCESS",
			fmt.Sprintf("Tái sử dụng artifact đã phê duyệt (Digest: %s)", entry.ArtifactDigest))
		return
	}

	// 3. Tải về vùng Quarantine độc lập
	s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StateFetching })
	qw, err := PrepareQuarantine(ctx, s.quarantineDir, reqID, req.CanonicalRepo, commitSHA)
	if err != nil {
		s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateFailed
			r.Error = fmt.Sprintf("Lỗi tải về quarantine: %v", err)
		})
		s.audit.LogEvent("SYSTEM", "QUARANTINE_FETCH_FAILED", "REPOSITORY", req.CanonicalRepo, "FAILED", err.Error())
		return
	}
	defer qw.Cleanup()

	// 4. Quét bảo mật (Trivy + Scorecard)
	s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StateScanning })
	report, err := RunTrivyScan(ctx, qw.SourceDir)
	if err != nil {
		s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateFailed
			r.Error = fmt.Sprintf("Lỗi tiến trình quét Trivy: %v", err)
		})
		s.audit.LogEvent("SYSTEM", "SCAN_FAILED", "COMMIT", commitSHA, "FAILED", err.Error())
		return
	}

	// OpenSSF Scorecard (tín hiệu bổ sung)
	scorecard := EvaluateScorecard(ctx, req.CanonicalRepo, commitSHA)
	report.Scorecard = scorecard

	// 5. Đánh giá chính sách bảo mật
	s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StatePolicyEvaluation })
	verdict, reasons := EvaluatePolicy(report)
	report.Verdict = verdict
	report.PolicyReasons = reasons

	var artifactDigest string
	var artifactSize int64

	// Nếu được Auto-Approved -> Lưu trữ artifact vào Approved Store
	if verdict == StateAutoApproved {
		entry, errStore := s.store.StoreApproved(req.CanonicalRepo, commitSHA, report.ScannerVersion, report.DBVersion, "BOT_AUTO_APPROVAL", qw)
		if errStore == nil {
			artifactDigest = entry.ArtifactDigest
			artifactSize = entry.ArtifactSize
		}
	}

	s.updateRequest(reqID, func(r *RepositoryRequest) {
		r.State = verdict
		r.Report = report
		r.ArtifactDigest = artifactDigest
		r.ArtifactSize = artifactSize
	})

	s.audit.LogEvent("BOT_SECURITY", string(verdict), "COMMIT", commitSHA, "SUCCESS",
		fmt.Sprintf("Kết quả đánh giá chính sách cho %s: %s", req.CanonicalRepo, verdict))
}

// AdminDecision xử lý phê duyệt hoặc từ chối thủ công từ Admin
func (s *GuardService) AdminDecision(reqID, decision, reviewer, reason string) (*RepositoryRequest, error) {
	s.mu.RLock()
	_, ok := s.requests[reqID]
	s.mu.RUnlock()
	if !ok {
		return nil, errors.New("không tìm thấy yêu cầu")
	}

	if decision != "APPROVED" && decision != "REJECTED" {
		return nil, errors.New("quyết định phải là APPROVED hoặc REJECTED")
	}

	now := time.Now()
	var finalState ScanState
	if decision == "APPROVED" {
		finalState = StateManuallyApproved
	} else {
		finalState = StateRejected
	}

	updated := s.updateRequest(reqID, func(r *RepositoryRequest) {
		r.State = finalState
		r.Decision = &DecisionRecord{
			Verdict:    finalState,
			Reviewer:   reviewer,
			ReviewedAt: now,
			Reason:     reason,
		}
	})

	s.audit.LogEvent(reviewer, fmt.Sprintf("ADMIN_%s", decision), "REQUEST", reqID, "SUCCESS",
		fmt.Sprintf("Admin %s đã đưa ra quyết định %s (Lý do: %s)", reviewer, decision, reason))

	return updated, nil
}

// Deliver phân phối mã nguồn đã được phê duyệt vào thư mục đích của workspace
func (s *GuardService) Deliver(reqID, destDir string) error {
	s.mu.RLock()
	req, ok := s.requests[reqID]
	s.mu.RUnlock()
	if !ok {
		return errors.New("không tìm thấy yêu cầu")
	}

	if req.State != StateAutoApproved && req.State != StateManuallyApproved {
		return fmt.Errorf("không thể phân phối mã khi trạng thái là %s (chỉ phân phối khi đã được phê duyệt)", req.State)
	}

	// Lấy artifact từ store
	entry, found := s.store.GetApproved(req.CanonicalRepo, req.CommitSHA)
	if !found {
		return errors.New("không tìm thấy gói artifact đã được duyệt trong kho lưu trữ an toàn")
	}

	// Giải nén có kiểm tra toàn vẹn
	if err := DeliverArtifact(entry.ArtifactPath, entry.ArtifactDigest, destDir); err != nil {
		s.audit.LogEvent("SYSTEM", "DELIVERY_FAILED", "WORKSPACE", destDir, "FAILED", err.Error())
		return err
	}

	s.updateRequest(reqID, func(r *RepositoryRequest) {
		r.State = StateDelivered
	})

	s.audit.LogEvent("SYSTEM", "ARTIFACT_DELIVERED", "WORKSPACE", destDir, "SUCCESS",
		fmt.Sprintf("Phân phối thành công commit %s vào %s", req.CommitSHA, destDir))

	return nil
}

// ListRequests trả về danh sách các yêu cầu
func (s *GuardService) ListRequests(submitter string, isAdmin bool) []*RepositoryRequest {
	s.mu.RLock()
	defer s.mu.RUnlock()

	var list []*RepositoryRequest
	for _, req := range s.requests {
		if isAdmin || req.Submitter == submitter {
			list = append(list, req)
		}
	}
	return list
}

// GetRequest trả về thông tin chi tiết một yêu cầu
func (s *GuardService) GetRequest(id string) (*RepositoryRequest, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	req, ok := s.requests[id]
	return req, ok
}

// GetAuditEvents trả về các sự kiện gần đây
func (s *GuardService) GetAuditEvents(limit int) []AuditLogEvent {
	return s.audit.GetRecentEvents(limit)
}
