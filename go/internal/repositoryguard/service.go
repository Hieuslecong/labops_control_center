package repositoryguard

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// ErrPermissionDenied indicates the caller does not own the request and is not an Admin.
var ErrPermissionDenied = errors.New("PERMISSION_DENIED: caller is not authorized for this repository request")

type GuardService struct {
	dataDir               string
	quarantineDir         string
	store                 *ApprovedArtifactStore
	audit                 *AuditLogger
	requestsPath          string
	allowedWorkspaceRoots []string
	allowLoopbackHTTP     bool
	scanFn                func(ctx context.Context, sourceDir string) (*ScanReport, error)
	mu                    sync.RWMutex
	requests              map[string]*RepositoryRequest
}

func NewGuardService(dataDir string) (*GuardService, error) {
	if err := os.MkdirAll(dataDir, 0750); err != nil {
		return nil, fmt.Errorf("không thể tạo dataDir: %w", err)
	}

	quarantineDir := filepath.Join(dataDir, "quarantine")
	if err := os.MkdirAll(quarantineDir, 0750); err != nil {
		return nil, fmt.Errorf("không thể tạo quarantineDir: %w", err)
	}

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
		scanFn:        RunTrivyScan,
		requests:      make(map[string]*RepositoryRequest),
	}

	if err := svc.loadRequests(); err != nil {
		return nil, fmt.Errorf("fail-closed: không thể nạp requests.json: %w", err)
	}
	svc.recoverStaleJobs()

	return svc, nil
}

// SetWorkspaceRoots confines delivered destinations to approved host workspace parent roots.
func (s *GuardService) SetWorkspaceRoots(roots []string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	copied := make([]string, 0, len(roots))
	for _, r := range roots {
		if trimmed := strings.TrimSpace(r); trimmed != "" {
			copied = append(copied, filepath.Clean(trimmed))
		}
	}
	s.allowedWorkspaceRoots = copied
}

// SetAllowLoopbackForTest enables local httptest Git servers in isolated tests only.
func (s *GuardService) SetAllowLoopbackForTest(allow bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.allowLoopbackHTTP = allow
}

// SetScanFuncForTest swaps the vulnerability scanner in offline/unit tests.
func (s *GuardService) SetScanFuncForTest(fn func(ctx context.Context, sourceDir string) (*ScanReport, error)) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if fn == nil {
		s.scanFn = RunTrivyScan
		return
	}
	s.scanFn = fn
}

// IsApprovedState returns true when a request state authorizes workspace artifact delivery.
func (s *GuardService) IsApprovedState(state ScanState) bool {
	return state == StateAutoApproved || state == StateManuallyApproved || state == StateApproved
}

func cloneRequest(req *RepositoryRequest) *RepositoryRequest {
	if req == nil {
		return nil
	}
	cp := *req
	if req.DeliveredAt != nil {
		t := *req.DeliveredAt
		cp.DeliveredAt = &t
	}
	if req.ExpiresAt != nil {
		t := *req.ExpiresAt
		cp.ExpiresAt = &t
	}
	if req.Decision != nil {
		d := *req.Decision
		cp.Decision = &d
	}
	if req.Report != nil {
		rep := *req.Report
		if len(req.Report.PolicyReasons) > 0 {
			rep.PolicyReasons = append([]string(nil), req.Report.PolicyReasons...)
		}
		if len(req.Report.Findings) > 0 {
			rep.Findings = append([]FindingItem(nil), req.Report.Findings...)
		}
		cp.Report = &rep
	}
	return &cp
}

func (s *GuardService) generateID() string {
	b := make([]byte, 6)
	_, _ = rand.Read(b)
	return fmt.Sprintf("req-%s", hex.EncodeToString(b))
}

func (s *GuardService) loadRequests() error {
	s.mu.Lock()
	defer s.mu.Unlock()

	data, err := os.ReadFile(s.requestsPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	if len(strings.TrimSpace(string(data))) == 0 {
		return nil
	}

	var list []*RepositoryRequest
	if err := json.Unmarshal(data, &list); err != nil {
		return err
	}

	for _, req := range list {
		if req != nil && req.ID != "" {
			s.requests[req.ID] = req
		}
	}
	return nil
}

func (s *GuardService) saveRequestsLocked() error {
	list := make([]*RepositoryRequest, 0, len(s.requests))
	for _, req := range s.requests {
		list = append(list, req)
	}

	data, err := json.MarshalIndent(list, "", "  ")
	if err != nil {
		return err
	}

	tmp, err := os.CreateTemp(s.dataDir, "requests.json.tmp.*")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	cleanup := true
	defer func() {
		if cleanup {
			_ = tmp.Close()
			_ = os.Remove(tmpPath)
		}
	}()

	if err := tmp.Chmod(0640); err != nil {
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		return err
	}
	if err := tmp.Sync(); err != nil {
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmpPath, s.requestsPath); err != nil {
		return err
	}
	cleanup = false
	return nil
}

func (s *GuardService) recoverStaleJobs() {
	s.mu.Lock()
	defer s.mu.Unlock()

	modified := false
	now := time.Now()
	for _, req := range s.requests {
		switch req.State {
		case StateRequested, StateSubmitted, StateValidating,
			StateResolvingCommit, StateCheckingCache, StateFetching,
			StateScanning, StatePolicyEvaluation, StatePublishing:
			req.State = StateFailed
			req.Error = "PROCESS_INTERRUPTED: Tiến trình bị gián đoạn do khởi động lại dịch vụ (Durable Queue Recovery)"
			req.UpdatedAt = now
			modified = true
		case StateDelivering:
			req.State = StateDeliveryFailed
			req.DeliveryStatus = "INTERRUPTED"
			req.Error = "PROCESS_INTERRUPTED: Tiến trình phân phối workspace bị gián đoạn khi dịch vụ khởi động lại"
			req.UpdatedAt = now
			modified = true
		}
	}
	if modified {
		_ = s.saveRequestsLocked()
	}
}

func (s *GuardService) validateRepoInput(rawURL string) (canonical, owner, repo string, err error) {
	canonical, owner, repo, err = NormalizeAndValidateURL(rawURL)
	if err == nil {
		return canonical, owner, repo, nil
	}

	s.mu.RLock()
	allowLoop := s.allowLoopbackHTTP
	s.mu.RUnlock()

	if !allowLoop {
		return "", "", "", err
	}

	u, parseErr := url.Parse(strings.TrimSpace(rawURL))
	if parseErr != nil {
		return "", "", "", err
	}
	scheme := strings.ToLower(u.Scheme)
	host := strings.ToLower(u.Hostname())
	if (scheme != "http" && scheme != "https") || (host != "127.0.0.1" && host != "localhost") || u.User != nil {
		return "", "", "", err
	}

	trimmed := strings.Trim(u.Path, "/")
	cleanNoGit := strings.TrimSuffix(trimmed, ".git")
	parts := strings.Split(cleanNoGit, "/")
	if len(parts) != 2 || !repoPathRegex.MatchString(cleanNoGit) {
		return "", "", "", err
	}
	// Preserve exact loopback URL (including .git if present) so httptest dumb HTTP serves directly.
	canonical = fmt.Sprintf("%s://%s%s", scheme, u.Host, strings.TrimRight(u.Path, "/"))
	return canonical, parts[0], parts[1], nil
}

// SubmitRequest tiếp nhận yêu cầu clone repo từ Researcher hoặc Admin.
func (s *GuardService) SubmitRequest(ctx context.Context, rawURL, ref, submitter, targetWorkspace string) (*RepositoryRequest, error) {
	submitter = strings.TrimSpace(submitter)
	if submitter == "" {
		return nil, errors.New("submitter identity is required")
	}

	canonicalURL, owner, repo, err := s.validateRepoInput(rawURL)
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
		State:           StateRequested,
		PolicyVersion:   CurrentPolicyVersion,
	}

	s.mu.Lock()
	s.requests[reqID] = req
	if saveErr := s.saveRequestsLocked(); saveErr != nil {
		delete(s.requests, reqID)
		s.mu.Unlock()
		return nil, fmt.Errorf("không thể lưu trạng thái yêu cầu: %w", saveErr)
	}
	snap := cloneRequest(req)
	s.mu.Unlock()

	s.audit.LogEvent(submitter, "REPO_REQUEST_SUBMITTED", "REPOSITORY", canonicalURL, "SUCCESS",
		fmt.Sprintf("Yêu cầu kiểm duyệt repo %s (ref: %s)", canonicalURL, ref))

	go s.processRequest(context.Background(), reqID)

	return snap, nil
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
	_ = s.saveRequestsLocked()
	return cloneRequest(req)
}

func (s *GuardService) getRequestMeta(reqID string) (canonicalRepo, requestedRef string, ok bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	req, exists := s.requests[reqID]
	if !exists || req == nil {
		return "", "", false
	}
	return req.CanonicalRepo, req.RequestedRef, true
}

func (s *GuardService) processRequest(ctx context.Context, reqID string) {
	s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StateResolvingCommit })

	canonicalRepo, requestedRef, ok := s.getRequestMeta(reqID)
	if !ok {
		return
	}

	commitSHA, err := ResolveCommitSHA(ctx, canonicalRepo, requestedRef)
	if err != nil {
		s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateFailed
			r.Error = fmt.Sprintf("Lỗi phân giải commit SHA: %v", err)
		})
		s.audit.LogEvent("SYSTEM", "COMMIT_RESOLUTION_FAILED", "REPOSITORY", canonicalRepo, "FAILED", err.Error())
		return
	}

	s.updateRequest(reqID, func(r *RepositoryRequest) {
		r.CommitSHA = commitSHA
		r.State = StateCheckingCache
	})

	// 2. Kiểm tra Approved Cache
	if entry, hit := s.store.GetApproved(canonicalRepo, commitSHA); hit {
		expires := entry.ApprovedAt.Add(30 * 24 * time.Hour)
		s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateAutoApproved
			r.CacheHit = true
			r.ArtifactDigest = entry.ArtifactDigest
			r.ArtifactSize = entry.ArtifactSize
			r.ScannerVersion = entry.ScannerVersion
			r.DBVersion = entry.DBVersion
			r.ArtifactProvenance = fmt.Sprintf("store:%s@%s", entry.CommitSHA, entry.ArtifactDigest)
			r.ExpiresAt = &expires
		})
		s.audit.LogEvent("SYSTEM", "CACHE_HIT", "COMMIT", commitSHA, "SUCCESS",
			fmt.Sprintf("Tái sử dụng artifact đã phê duyệt (Digest: %s)", entry.ArtifactDigest))
		return
	}

	// 3. Tải về vùng Quarantine độc lập & đóng gói Git Bundle
	s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StateFetching })
	qw, err := PrepareQuarantineWithCanonical(ctx, s.quarantineDir, reqID, canonicalRepo, canonicalRepo, commitSHA)
	if err != nil {
		s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateFailed
			r.Error = fmt.Sprintf("Lỗi tải về quarantine: %v", err)
		})
		s.audit.LogEvent("SYSTEM", "QUARANTINE_FETCH_FAILED", "REPOSITORY", canonicalRepo, "FAILED", err.Error())
		return
	}

	// 4. Quét bảo mật
	s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StateScanning })
	s.mu.RLock()
	scanner := s.scanFn
	s.mu.RUnlock()
	if scanner == nil {
		scanner = RunTrivyScan
	}

	report, err := scanner(ctx, qw.SourceDir)
	if err != nil {
		_ = qw.PurgeAll()
		s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateFailed
			r.Error = fmt.Sprintf("Lỗi tiến trình quét bảo mật: %v", err)
		})
		s.audit.LogEvent("SYSTEM", "SCAN_FAILED", "COMMIT", commitSHA, "FAILED", err.Error())
		return
	}
	if report.Coverage.Submodules == "" || report.Coverage.LFS == "" {
		subSt, lfsSt := InspectAuxiliaryFeatures(qw.SourceDir)
		if report.Coverage.Submodules == "" {
			report.Coverage.Submodules = subSt
		}
		if report.Coverage.LFS == "" {
			report.Coverage.LFS = lfsSt
		}
	}

	// OpenSSF Scorecard (tín hiệu bổ sung)
	scorecard := EvaluateScorecard(ctx, canonicalRepo, commitSHA)
	report.Scorecard = scorecard

	// 5. Đánh giá chính sách bảo mật
	s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StatePolicyEvaluation })
	verdict, reasons := EvaluatePolicy(report)
	report.Verdict = verdict
	report.PolicyReasons = reasons

	artifactDigest := qw.DigestSHA256
	artifactSize := qw.TotalSize
	provenance := fmt.Sprintf("quarantine:%s", qw.DigestSHA256)
	var expiresAt *time.Time

	switch verdict {
	case StateAutoApproved:
		entry, errStore := s.store.StoreApproved(canonicalRepo, commitSHA, report.ScannerVersion, report.DBVersion, "BOT_AUTO_APPROVAL", qw)
		_ = qw.PurgeAll()
		if errStore != nil {
			s.updateRequest(reqID, func(r *RepositoryRequest) {
				r.State = StateFailed
				r.Report = report
				r.Error = fmt.Sprintf("STORE_APPROVED_FAILED: %v", errStore)
			})
			s.audit.LogEvent("BOT_SECURITY", "STORE_APPROVED_FAILED", "COMMIT", commitSHA, "FAILED", errStore.Error())
			return
		}
		artifactDigest = entry.ArtifactDigest
		artifactSize = entry.ArtifactSize
		provenance = fmt.Sprintf("store:%s@%s", entry.CommitSHA, entry.ArtifactDigest)
		exp := entry.ApprovedAt.Add(30 * 24 * time.Hour)
		expiresAt = &exp

	case StateReviewRequired:
		// Xóa mã nguồn bung thô, giữ lại archive 0440 trong quarantine chờ Admin phê duyệt
		_ = qw.Cleanup()

	default:
		_ = qw.PurgeAll()
	}

	s.updateRequest(reqID, func(r *RepositoryRequest) {
		r.State = verdict
		r.Report = report
		r.ArtifactDigest = artifactDigest
		r.ArtifactSize = artifactSize
		r.ScannerVersion = report.ScannerVersion
		r.DBVersion = report.DBVersion
		r.ArtifactProvenance = provenance
		r.ExpiresAt = expiresAt
	})

	s.audit.LogEvent("BOT_SECURITY", string(verdict), "COMMIT", commitSHA, "SUCCESS",
		fmt.Sprintf("Kết quả đánh giá chính sách cho %s: %s", canonicalRepo, verdict))
}

// AdminDecision xử lý phê duyệt hoặc từ chối thủ công từ Admin với khóa nguyên tử và xác minh toàn vẹn quarantine.
func (s *GuardService) AdminDecision(reqID, decision, reviewer, reason string) (*RepositoryRequest, error) {
	if decision != "APPROVED" && decision != "REJECTED" {
		return nil, errors.New("quyết định phải là APPROVED hoặc REJECTED")
	}
	reviewer = strings.TrimSpace(reviewer)
	if reviewer == "" {
		return nil, errors.New("reviewer identity is required")
	}

	s.mu.Lock()
	req, ok := s.requests[reqID]
	if !ok || req == nil {
		s.mu.Unlock()
		return nil, errors.New("không tìm thấy yêu cầu")
	}
	if req.State != StateReviewRequired {
		currentState := req.State
		s.mu.Unlock()
		return nil, fmt.Errorf("INVALID_STATE_TRANSITION: yêu cầu đang ở trạng thái %s, chỉ có thể quyết định khi là REVIEW_REQUIRED", currentState)
	}

	now := time.Now()
	if decision == "REJECTED" {
		req.State = StateRejected
		req.UpdatedAt = now
		req.Decision = &DecisionRecord{
			Verdict:    StateRejected,
			Reviewer:   reviewer,
			ReviewedAt: now,
			Reason:     reason,
		}
		_ = s.saveRequestsLocked()
		snap := cloneRequest(req)
		s.mu.Unlock()

		_ = os.RemoveAll(filepath.Join(s.quarantineDir, reqID))
		s.audit.LogEvent(reviewer, "ADMIN_REJECTED", "REQUEST", reqID, "SUCCESS",
			fmt.Sprintf("Admin %s đã REJECTED (Lý do: %s)", reviewer, reason))
		return snap, nil
	}

	// Decision == APPROVED: Atomically claim StatePublishing under mutex
	req.State = StatePublishing
	req.UpdatedAt = now
	_ = s.saveRequestsLocked()

	canonicalRepo := req.CanonicalRepo
	commitSHA := req.CommitSHA
	expectedDigest := req.ArtifactDigest
	scannerVer := req.ScannerVersion
	dbVer := req.DBVersion
	if scannerVer == "" && req.Report != nil {
		scannerVer = req.Report.ScannerVersion
	}
	if dbVer == "" && req.Report != nil {
		dbVer = req.Report.DBVersion
	}
	s.mu.Unlock()

	var entry *CacheEntry
	if cached, found := s.store.GetApproved(canonicalRepo, commitSHA); found {
		entry = cached
	} else {
		archiveFile := filepath.Join(s.quarantineDir, reqID, "source.tar.gz")
		failPublish := func(errMsg string) (*RepositoryRequest, error) {
			_ = os.RemoveAll(filepath.Join(s.quarantineDir, reqID))
			s.updateRequest(reqID, func(r *RepositoryRequest) {
				r.State = StateFailed
				r.Error = errMsg
			})
			s.audit.LogEvent(reviewer, "ADMIN_APPROVED", "REQUEST", reqID, "FAILED", errMsg)
			return nil, errors.New(errMsg)
		}

		if strings.TrimSpace(expectedDigest) == "" {
			return failPublish("QUARANTINE_INTEGRITY_FAILED: thiếu digest kiểm chứng trước khi xuất bản")
		}
		stat, statErr := os.Lstat(archiveFile)
		if statErr != nil || stat.Mode()&os.ModeSymlink != 0 || !stat.Mode().IsRegular() {
			return failPublish(fmt.Sprintf("QUARANTINE_INTEGRITY_FAILED: archive quarantine bị thiếu hoặc không hợp lệ (%v)", statErr))
		}
		actualDigest, hashErr := HashFileSHA256(archiveFile)
		if hashErr != nil {
			return failPublish(fmt.Sprintf("QUARANTINE_INTEGRITY_FAILED: lỗi tính checksum quarantine (%v)", hashErr))
		}
		if !strings.EqualFold(actualDigest, expectedDigest) {
			return failPublish(fmt.Sprintf("QUARANTINE_INTEGRITY_FAILED: archive bị chỉnh sửa ngoài ý muốn (expected=%s, actual=%s)", expectedDigest, actualDigest))
		}

		qw := &QuarantineWorkspace{
			BaseDir:      filepath.Join(s.quarantineDir, reqID),
			ArchiveFile:  archiveFile,
			DigestSHA256: expectedDigest,
			TotalSize:    stat.Size(),
		}
		stored, errStore := s.store.StoreApproved(canonicalRepo, commitSHA, scannerVer, dbVer, reviewer, qw)
		if errStore != nil {
			return failPublish(fmt.Sprintf("STORE_APPROVED_FAILED: %v", errStore))
		}
		entry = stored
	}

	_ = os.RemoveAll(filepath.Join(s.quarantineDir, reqID))

	expires := entry.ApprovedAt.Add(30 * 24 * time.Hour)
	decisionTime := time.Now()
	updated := s.updateRequest(reqID, func(r *RepositoryRequest) {
		r.State = StateManuallyApproved
		r.ArtifactDigest = entry.ArtifactDigest
		r.ArtifactSize = entry.ArtifactSize
		r.ScannerVersion = scannerVer
		r.DBVersion = dbVer
		r.ArtifactProvenance = fmt.Sprintf("store:%s@%s", entry.CommitSHA, entry.ArtifactDigest)
		r.ExpiresAt = &expires
		r.Error = ""
		r.Decision = &DecisionRecord{
			Verdict:    StateManuallyApproved,
			Reviewer:   reviewer,
			ReviewedAt: decisionTime,
			Reason:     reason,
		}
	})

	s.audit.LogEvent(reviewer, "ADMIN_APPROVED", "REQUEST", reqID, "SUCCESS",
		fmt.Sprintf("Admin %s đã APPROVED (Lý do: %s)", reviewer, reason))

	return updated, nil
}

// RevokeApproval revokes an approved request and removes its cached artifact from the approved store.
func (s *GuardService) RevokeApproval(reqID, actor, reason string) error {
	s.mu.Lock()
	req, ok := s.requests[reqID]
	if !ok || req == nil {
		s.mu.Unlock()
		return errors.New("không tìm thấy yêu cầu")
	}
	canonicalRepo := req.CanonicalRepo
	commitSHA := req.CommitSHA
	now := time.Now()
	req.State = StateRevoked
	req.UpdatedAt = now
	req.Decision = &DecisionRecord{
		Verdict:    StateRevoked,
		Reviewer:   actor,
		ReviewedAt: now,
		Reason:     reason,
	}
	_ = s.saveRequestsLocked()
	s.mu.Unlock()

	_ = s.store.RevokeEntry(canonicalRepo, commitSHA)
	s.audit.LogEvent(actor, "APPROVAL_REVOKED", "REQUEST", reqID, "SUCCESS", reason)
	return nil
}

// Deliver phân phối mã nguồn đã được phê duyệt vào thư mục đích (System/Admin context).
func (s *GuardService) Deliver(reqID, destDir string) error {
	return s.DeliverAuthorized(reqID, "SYSTEM", true, destDir)
}

// DeliverAuthorized enforces ownership/approval state and performs atomic CAS transition + strict Git delivery.
func (s *GuardService) DeliverAuthorized(reqID, actor string, isAdmin bool, destDir string) error {
	s.mu.Lock()
	req, ok := s.requests[reqID]
	if !ok || req == nil {
		s.mu.Unlock()
		return errors.New("không tìm thấy yêu cầu")
	}

	if !isAdmin && (strings.TrimSpace(actor) == "" || req.Submitter != actor) {
		s.mu.Unlock()
		s.audit.LogEvent(actor, "DELIVERY_DENIED", "REQUEST", reqID, "DENIED",
			"Caller attempted to deliver another user's repository request")
		return ErrPermissionDenied
	}

	if req.ExpiresAt != nil && time.Now().After(*req.ExpiresAt) {
		req.State = StateExpired
		req.UpdatedAt = time.Now()
		_ = s.saveRequestsLocked()
		s.mu.Unlock()
		return errors.New("APPROVED_ARTIFACT_EXPIRED: phê duyệt đã hết hạn, vui lòng gửi yêu cầu quét lại")
	}

	if !s.IsApprovedState(req.State) && req.State != StateDeliveryFailed {
		state := req.State
		s.mu.Unlock()
		return fmt.Errorf("không thể phân phối mã khi trạng thái là %s (chỉ phân phối khi đã được phê duyệt)", state)
	}

	req.State = StateDelivering
	req.DeliveryStatus = "DELIVERING"
	req.DeliveryPath = destDir
	req.UpdatedAt = time.Now()
	if err := s.saveRequestsLocked(); err != nil {
		s.mu.Unlock()
		return err
	}

	canonicalRepo := req.CanonicalRepo
	commitSHA := req.CommitSHA
	allowedRoots := append([]string(nil), s.allowedWorkspaceRoots...)
	s.mu.Unlock()

	entry, found := s.store.GetApproved(canonicalRepo, commitSHA)
	if !found {
		err := errors.New("không tìm thấy gói artifact đã được duyệt trong kho lưu trữ an toàn")
		s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateDeliveryFailed
			r.DeliveryStatus = "FAILED"
			r.Error = err.Error()
		})
		s.audit.LogEvent(actor, "DELIVERY_FAILED", "WORKSPACE", destDir, "FAILED", err.Error())
		return err
	}

	if err := DeliverArtifactStrict(context.Background(), entry.ArtifactPath, entry.ArtifactDigest, commitSHA, destDir, allowedRoots); err != nil {
		s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateDeliveryFailed
			r.DeliveryStatus = "FAILED"
			r.Error = err.Error()
		})
		s.audit.LogEvent(actor, "DELIVERY_FAILED", "WORKSPACE", destDir, "FAILED", err.Error())
		return err
	}

	now := time.Now()
	s.updateRequest(reqID, func(r *RepositoryRequest) {
		r.State = StateDelivered
		r.DeliveredAt = &now
		r.DeliveryPath = destDir
		r.DeliveryStatus = "DELIVERED"
		r.Error = ""
	})

	s.audit.LogEvent(actor, "ARTIFACT_DELIVERED", "WORKSPACE", destDir, "SUCCESS",
		fmt.Sprintf("Phân phối thành công commit %s vào %s", commitSHA, destDir))

	return nil
}

// ListRequests trả về bản sao danh sách các yêu cầu theo phạm vi RBAC.
func (s *GuardService) ListRequests(submitter string, isAdmin bool) []*RepositoryRequest {
	s.mu.RLock()
	defer s.mu.RUnlock()

	var list []*RepositoryRequest
	for _, req := range s.requests {
		if isAdmin || req.Submitter == submitter {
			list = append(list, cloneRequest(req))
		}
	}
	return list
}

// GetRequest trả về bản sao chi tiết một yêu cầu.
func (s *GuardService) GetRequest(id string) (*RepositoryRequest, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	req, ok := s.requests[id]
	if !ok {
		return nil, false
	}
	return cloneRequest(req), true
}

// GetAuditEvents trả về các sự kiện gần đây.
func (s *GuardService) GetAuditEvents(limit int) []AuditLogEvent {
	return s.audit.GetRecentEvents(limit)
}
