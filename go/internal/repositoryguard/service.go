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
	"regexp"
	"sort"
	"strings"
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
	persistErr    error
}

var safeGeneratedRequestID = regexp.MustCompile(`^req-[a-f0-9]{12}$`)
var safeIdempotencyKey = regexp.MustCompile(`^[A-Za-z0-9_-]{16,128}$`)

func NewGuardService(dataDir string) (*GuardService, error) {
	if dataDir == "" {
		return nil, errors.New("repository guard data directory is required")
	}
	dataDir, err := filepath.Abs(dataDir)
	if err != nil {
		return nil, fmt.Errorf("resolve dataDir: %w", err)
	}
	if err := os.MkdirAll(dataDir, 0750); err != nil {
		return nil, fmt.Errorf("không thể tạo dataDir: %w", err)
	}
	info, err := os.Lstat(dataDir)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("repository guard data directory must be a real directory")
	}
	canonical, err := filepath.EvalSymlinks(dataDir)
	if err != nil || canonical != dataDir {
		return nil, errors.New("repository guard data directory must be canonical")
	}

	quarantineDir := filepath.Join(dataDir, "quarantine")
	if err := os.MkdirAll(quarantineDir, 0750); err != nil {
		return nil, fmt.Errorf("cannot create quarantine directory: %w", err)
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
		requests:      make(map[string]*RepositoryRequest),
	}

	if err := svc.loadRequests(); err != nil {
		return nil, fmt.Errorf("load repository request state: %w", err)
	}
	if err := svc.recoverStaleJobs(); err != nil {
		return nil, fmt.Errorf("recover repository request state: %w", err)
	}

	return svc, nil
}

func (s *GuardService) generateID() (string, error) {
	b := make([]byte, 6)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return fmt.Sprintf("req-%s", hex.EncodeToString(b)), nil
}

func (s *GuardService) loadRequests() error {
	info, err := os.Lstat(s.requestsPath)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return errors.New("request state must be a regular file")
	}
	data, err := os.ReadFile(s.requestsPath)
	if err != nil {
		return err
	}
	var list []*RepositoryRequest
	if err := json.Unmarshal(data, &list); err != nil {
		return err
	}
	if list == nil {
		return errors.New("request state must be a JSON array")
	}
	for _, req := range list {
		if req == nil || req.ID == "" || len(req.ID) > 64 || req.Submitter == "" {
			return errors.New("request state contains an invalid record")
		}
		if _, exists := s.requests[req.ID]; exists {
			return errors.New("request state contains duplicate IDs")
		}
		if req.CanonicalRepo != "" {
			canonical, _, _, err := NormalizeAndValidateURL(req.CanonicalRepo)
			if err != nil || canonical != req.CanonicalRepo {
				return errors.New("request state contains a non-canonical repository URL")
			}
		}
		s.requests[req.ID] = req
	}
	return nil
}

func (s *GuardService) saveRequests() error {
	var list []*RepositoryRequest
	for _, req := range s.requests {
		list = append(list, req)
	}
	sort.Slice(list, func(i, j int) bool { return list[i].ID < list[j].ID })

	data, err := json.MarshalIndent(list, "", "  ")
	if err != nil {
		return err
	}

	return atomicStoreWrite(s.requestsPath, data, 0640)
}

func (s *GuardService) recoverStaleJobs() error {
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
		if err := s.saveRequests(); err != nil {
			s.persistErr = err
			return err
		}
	}
	return nil
}

// SubmitRequest tiếp nhận yêu cầu clone repo từ Researcher hoặc Admin
func (s *GuardService) SubmitRequest(ctx context.Context, rawURL, ref, submitter, targetWorkspace string) (*RepositoryRequest, error) {
	return s.SubmitRequestWithKey(ctx, rawURL, ref, submitter, targetWorkspace, "")
}

func (s *GuardService) SubmitRequestWithKey(ctx context.Context, rawURL, ref, submitter, targetWorkspace, idempotencyKey string) (*RepositoryRequest, error) {
	canonicalURL, owner, repo, err := NormalizeAndValidateURL(rawURL)
	if err != nil {
		return nil, fmt.Errorf("URL không hợp lệ: %w", err)
	}
	ref = strings.TrimSpace(ref)
	if ref == "" {
		ref = "HEAD"
	}
	if !validRequestedRef(ref) && !validCommitSHA(ref) {
		return nil, errors.New("repository reference is invalid")
	}
	if !subjectTokenIDRE.MatchString(submitter) || !workspaceTokenIDRE.MatchString(targetWorkspace) {
		return nil, errors.New("authenticated submitter and registered workspace are required")
	}
	if idempotencyKey != "" && !safeIdempotencyKey.MatchString(idempotencyKey) {
		return nil, errors.New("idempotency key must be 16-128 URL-safe characters")
	}

	s.mu.Lock()
	if s.persistErr != nil {
		s.mu.Unlock()
		return nil, fmt.Errorf("repository state unavailable: %w", s.persistErr)
	}
	if idempotencyKey != "" {
		for _, existing := range s.requests {
			if existing.IdempotencyKey != idempotencyKey || existing.Submitter != submitter || existing.TargetWorkspace != targetWorkspace {
				continue
			}
			if existing.CanonicalRepo != canonicalURL || existing.RequestedRef != ref {
				s.mu.Unlock()
				return nil, errors.New("idempotency key was reused for a different repository request")
			}
			copy := cloneRequest(existing)
			s.mu.Unlock()
			return copy, nil
		}
	}
	s.mu.Unlock()

	reqID, err := s.generateID()
	if err != nil {
		return nil, fmt.Errorf("generate request ID: %w", err)
	}
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
		IdempotencyKey:  idempotencyKey,
		SubmittedAt:     now,
		UpdatedAt:       now,
		State:           StateSubmitted,
		PolicyVersion:   CurrentPolicyVersion,
	}

	s.mu.Lock()
	if s.persistErr != nil {
		s.mu.Unlock()
		return nil, fmt.Errorf("repository state unavailable: %w", s.persistErr)
	}
	if idempotencyKey != "" {
		for _, existing := range s.requests {
			if existing.IdempotencyKey != idempotencyKey || existing.Submitter != submitter || existing.TargetWorkspace != targetWorkspace {
				continue
			}
			if existing.CanonicalRepo != canonicalURL || existing.RequestedRef != ref {
				s.mu.Unlock()
				return nil, errors.New("idempotency key was reused for a different repository request")
			}
			copy := cloneRequest(existing)
			s.mu.Unlock()
			return copy, nil
		}
	}
	s.requests[reqID] = req
	if err := s.saveRequests(); err != nil {
		delete(s.requests, reqID)
		s.persistErr = err
		s.mu.Unlock()
		return nil, fmt.Errorf("persist repository request: %w", err)
	}
	s.mu.Unlock()

	if err := s.audit.LogEvent(submitter, "REPO_REQUEST_SUBMITTED", "REPOSITORY", canonicalURL, "SUCCESS",
		fmt.Sprintf("Yêu cầu kiểm duyệt repo %s (ref: %s)", canonicalURL, ref)); err != nil {
		s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StateFailed; r.Error = "Audit persistence failed" })
		return nil, fmt.Errorf("persist repository audit event: %w", err)
	}

	// Chạy quy trình kiểm duyệt (xử lý bất đồng bộ nhưng durable)
	response := cloneRequest(req)
	go s.processRequest(context.Background(), reqID)

	return response, nil
}

func (s *GuardService) updateRequest(reqID string, fn func(r *RepositoryRequest)) *RepositoryRequest {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.persistErr != nil {
		return nil
	}

	req, ok := s.requests[reqID]
	if !ok {
		return nil
	}
	old := cloneRequest(req)
	fn(req)
	req.UpdatedAt = time.Now()
	if err := s.saveRequests(); err != nil {
		*req = *old
		s.persistErr = err
		return nil
	}
	return cloneRequest(req)
}

func cloneRequest(req *RepositoryRequest) *RepositoryRequest {
	if req == nil {
		return nil
	}
	copy := *req
	if req.Decision != nil {
		d := *req.Decision
		copy.Decision = &d
	}
	if req.Report != nil {
		r := *req.Report
		r.PolicyReasons = append([]string(nil), req.Report.PolicyReasons...)
		r.Findings = append([]FindingItem(nil), req.Report.Findings...)
		r.Scorecard.Reasons = append([]string(nil), req.Report.Scorecard.Reasons...)
		if req.Report.Scorecard.Checks != nil {
			r.Scorecard.Checks = make(map[string]int, len(req.Report.Scorecard.Checks))
			for k, v := range req.Report.Scorecard.Checks {
				r.Scorecard.Checks[k] = v
			}
		}
		copy.Report = &r
	}
	return &copy
}

func (s *GuardService) processRequest(ctx context.Context, reqID string) {
	// 1. Resolving commit SHA
	if s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StateResolvingCommit }) == nil {
		return
	}

	s.mu.RLock()
	req := cloneRequest(s.requests[reqID])
	s.mu.RUnlock()
	if req == nil {
		return
	}

	commitSHA, err := ResolveCommitSHA(ctx, req.CanonicalRepo, req.RequestedRef)
	if err != nil {
		_ = s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateFailed
			r.Error = fmt.Sprintf("Lỗi phân giải commit SHA: %v", err)
		})
		s.audit.LogEvent("SYSTEM", "COMMIT_RESOLUTION_FAILED", "REPOSITORY", req.CanonicalRepo, "FAILED", err.Error())
		return
	}

	if s.updateRequest(reqID, func(r *RepositoryRequest) {
		r.CommitSHA = commitSHA
		r.State = StateCheckingCache
	}) == nil {
		return
	}

	// 2. Kiểm tra Approved Cache
	if entry, hit := s.store.GetApproved(req.CanonicalRepo, commitSHA); hit {
		if bundle, bundledEntry, bundleErr := s.store.OpenBundle(req.CanonicalRepo, commitSHA); bundleErr == nil {
			_ = bundle.Close()
			if !strings.EqualFold(bundledEntry.ArtifactDigest, entry.ArtifactDigest) {
				_ = s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StateFailed; r.Error = "APPROVED_ARTIFACT_BINDING_MISMATCH" })
				return
			}
			if s.updateRequest(reqID, func(r *RepositoryRequest) {
				r.State = StateAutoApproved
				r.CacheHit = true
				r.ArtifactDigest = entry.ArtifactDigest
				r.ArtifactSize = entry.ArtifactSize
				r.GitBundleDigest = bundledEntry.BundleDigest
				r.GitBundleSize = bundledEntry.BundleSize
			}) == nil {
				return
			}
			_ = s.audit.LogEvent("SYSTEM", "CACHE_HIT", "COMMIT", commitSHA, "SUCCESS",
				fmt.Sprintf("Tái sử dụng artifact đã phê duyệt (Digest: %s)", entry.ArtifactDigest))
			return
		}
	}

	// 3. Tải về vùng Quarantine độc lập
	if s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StateFetching }) == nil {
		return
	}
	qw, err := PrepareQuarantine(ctx, s.quarantineDir, reqID, req.CanonicalRepo, commitSHA)
	if err != nil {
		_ = s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateFailed
			r.Error = fmt.Sprintf("Lỗi tải về quarantine: %v", err)
		})
		_ = s.audit.LogEvent("SYSTEM", "QUARANTINE_FETCH_FAILED", "REPOSITORY", req.CanonicalRepo, "FAILED", err.Error())
		return
	}
	defer qw.Cleanup()

	// 4. Quét bảo mật (Trivy + Scorecard)
	if s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StateScanning }) == nil {
		_ = qw.Cleanup()
		return
	}
	report, err := RunTrivyScan(ctx, qw.SourceDir)
	if err != nil {
		_ = s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateFailed
			r.Error = fmt.Sprintf("Lỗi tiến trình quét Trivy: %v", err)
		})
		_ = s.audit.LogEvent("SYSTEM", "SCAN_FAILED", "COMMIT", commitSHA, "FAILED", err.Error())
		return
	}

	// OpenSSF Scorecard (tín hiệu bổ sung)
	scorecard := EvaluateScorecard(ctx, req.CanonicalRepo, commitSHA)
	report.Scorecard = scorecard

	// 5. Đánh giá chính sách bảo mật
	if s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StatePolicyEvaluation }) == nil {
		return
	}
	verdict, reasons := EvaluatePolicy(report)
	report.Verdict = verdict
	report.PolicyReasons = reasons

	artifactDigest := qw.DigestSHA256
	artifactSize := qw.TotalSize
	gitBundleDigest := qw.BundleDigest
	gitBundleSize := qw.BundleSize

	// Nếu được Auto-Approved -> Lưu trữ artifact vào Approved Store
	if verdict == StateAutoApproved {
		entry, errStore := s.store.StoreApproved(req.CanonicalRepo, commitSHA, report.ScannerVersion, report.DBVersion, "BOT_AUTO_APPROVAL", qw)
		if errStore != nil {
			_ = s.updateRequest(reqID, func(r *RepositoryRequest) {
				r.State = StateFailed
				r.Error = "APPROVED_ARTIFACT_PUBLISH_FAILED: " + errStore.Error()
			})
			_ = s.audit.LogEvent("BOT_SECURITY", "ARTIFACT_PUBLISH_FAILED", "COMMIT", commitSHA, "FAILED", errStore.Error())
			return
		}
		artifactDigest = entry.ArtifactDigest
		artifactSize = entry.ArtifactSize
		gitBundleDigest = entry.BundleDigest
		gitBundleSize = entry.BundleSize
	} else if verdict == StateRejected {
		if err := qw.PurgeAll(); err != nil {
			_ = s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StateFailed; r.Error = "QUARANTINE_PURGE_FAILED: " + err.Error() })
			_ = s.audit.LogEvent("BOT_SECURITY", "QUARANTINE_PURGE_FAILED", "COMMIT", commitSHA, "FAILED", err.Error())
			return
		}
	}

	if s.updateRequest(reqID, func(r *RepositoryRequest) {
		r.State = verdict
		r.Report = report
		r.ArtifactDigest = artifactDigest
		r.ArtifactSize = artifactSize
		r.GitBundleDigest = gitBundleDigest
		r.GitBundleSize = gitBundleSize
	}) == nil {
		return
	}

	if err := s.audit.LogEvent("BOT_SECURITY", string(verdict), "COMMIT", commitSHA, "SUCCESS",
		fmt.Sprintf("Kết quả đánh giá chính sách cho %s: %s", req.CanonicalRepo, verdict)); err != nil {
		_ = s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StateFailed; r.Error = "AUDIT_PERSISTENCE_FAILED" })
	}
}

// AdminDecision xử lý phê duyệt hoặc từ chối thủ công từ Admin
func (s *GuardService) AdminDecision(reqID, decision, reviewer, reason string) (*RepositoryRequest, error) {
	if !safeGeneratedRequestID.MatchString(reqID) {
		return nil, errors.New("invalid repository request ID")
	}
	req, ok := s.GetRequest(reqID)
	if !ok {
		return nil, errors.New("không tìm thấy yêu cầu")
	}
	if req.State != StateReviewRequired {
		return nil, fmt.Errorf("request is not awaiting an admin decision (state=%s)", req.State)
	}
	if reviewer == "" || strings.TrimSpace(reason) == "" {
		return nil, errors.New("reviewer and decision reason are required")
	}

	if decision != "APPROVED" && decision != "REJECTED" {
		return nil, errors.New("quyết định phải là APPROVED hoặc REJECTED")
	}

	var finalState ScanState
	if decision == "APPROVED" {
		finalState = StateManuallyApproved
		if req.CommitSHA == "" || req.ArtifactDigest == "" || req.GitBundleDigest == "" || req.Report == nil {
			return nil, errors.New("approved request is missing commit, artifact digest, Git bundle, or scan report")
		}
		if _, found := s.store.GetApproved(req.CanonicalRepo, req.CommitSHA); !found {
			archiveFile := filepath.Join(s.quarantineDir, reqID, "source.tar.gz")
			info, err := os.Lstat(archiveFile)
			if err != nil || !info.Mode().IsRegular() {
				return nil, errors.New("approved quarantine artifact is unavailable or unsafe")
			}
			bundleFile := filepath.Join(s.quarantineDir, reqID, "repository.bundle")
			bundleInfo, bundleErr := os.Lstat(bundleFile)
			if bundleErr != nil || !bundleInfo.Mode().IsRegular() {
				return nil, errors.New("approved quarantine Git bundle is unavailable or unsafe")
			}
			qw := &QuarantineWorkspace{
				ArchiveFile: archiveFile, DigestSHA256: req.ArtifactDigest,
				BundleFile: bundleFile, BundleDigest: req.GitBundleDigest, BundleSize: bundleInfo.Size(),
			}
			entry, errStore := s.store.StoreApproved(req.CanonicalRepo, req.CommitSHA, req.Report.ScannerVersion, req.Report.DBVersion, reviewer, qw)
			if errStore != nil {
				return nil, fmt.Errorf("publish approved artifact: %w", errStore)
			}
			req.ArtifactDigest, req.ArtifactSize = entry.ArtifactDigest, entry.ArtifactSize
			req.GitBundleDigest, req.GitBundleSize = entry.BundleDigest, entry.BundleSize
		}
	} else {
		finalState = StateRejected
	}

	updated := s.updateRequest(reqID, func(r *RepositoryRequest) {
		r.State = finalState
		r.ArtifactDigest = req.ArtifactDigest
		r.ArtifactSize = req.ArtifactSize
		r.GitBundleDigest = req.GitBundleDigest
		r.GitBundleSize = req.GitBundleSize
		r.Decision = &DecisionRecord{
			Verdict:    finalState,
			Reviewer:   reviewer,
			ReviewedAt: time.Now(),
			Reason:     reason,
		}
	})
	if updated == nil {
		return nil, errors.New("could not persist repository decision")
	}

	if err := s.audit.LogEvent(reviewer, fmt.Sprintf("ADMIN_%s", decision), "REQUEST", reqID, "SUCCESS",
		fmt.Sprintf("Admin %s đã đưa ra quyết định %s (Lý do: %s)", reviewer, decision, reason)); err != nil {
		_ = s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StateFailed; r.Error = "AUDIT_PERSISTENCE_FAILED" })
		return nil, fmt.Errorf("persist decision audit: %w", err)
	}
	if finalState == StateRejected {
		if err := s.removeQuarantineRequest(reqID); err != nil {
			_ = s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StateFailed; r.Error = "QUARANTINE_PURGE_FAILED: " + err.Error() })
			_ = s.audit.LogEvent(reviewer, "QUARANTINE_PURGE_FAILED", "REQUEST", reqID, "FAILED", err.Error())
			return nil, fmt.Errorf("partial rejection cleanup failure: %w", err)
		}
	}

	result, found := s.GetRequest(reqID)
	if !found {
		return nil, errors.New("repository request disappeared after decision")
	}
	return result, nil
}

func (s *GuardService) removeQuarantineRequest(reqID string) error {
	if !safeGeneratedRequestID.MatchString(reqID) {
		return errors.New("invalid repository request ID")
	}
	root, err := os.OpenRoot(s.quarantineDir)
	if err != nil {
		return err
	}
	defer root.Close()
	return root.RemoveAll(reqID)
}

// Deliver phân phối mã nguồn đã được phê duyệt vào thư mục đích của workspace
func (s *GuardService) Deliver(reqID, destDir string) error {
	// The caller-controlled path cannot establish workspace or owner identity.
	return errors.New("repository delivery unavailable until trusted workspace authorization is implemented")
}

func (s *GuardService) OpenApprovedBundle(reqID string) (*os.File, string, string, error) {
	req, found := s.GetRequest(reqID)
	if !found || (req.State != StateAutoApproved && req.State != StateManuallyApproved) {
		return nil, "", "", errors.New("repository request is not approved")
	}
	f, entry, err := s.store.OpenBundle(req.CanonicalRepo, req.CommitSHA)
	if err != nil {
		return nil, "", "", err
	}
	if entry.BundleDigest != req.GitBundleDigest || entry.ArtifactDigest != req.ArtifactDigest || entry.BundleSize != req.GitBundleSize {
		_ = f.Close()
		return nil, "", "", errors.New("request and approved bundle metadata do not match")
	}
	return f, entry.BundleDigest, req.CommitSHA, nil
}

func (s *GuardService) recordDeliveryReadFailure(reqID, actor string, cause error) {
	if cause == nil {
		return
	}
	_ = s.audit.LogEvent(actor, "REPOSITORY_BUNDLE_TRANSFER_FAILED", "REQUEST", reqID, "FAILED", cause.Error())
}

// ListRequests trả về danh sách các yêu cầu
func (s *GuardService) ListRequests(submitter string, isAdmin bool) []*RepositoryRequest {
	return s.ListRequestsForWorkspace(submitter, isAdmin, "")
}

func (s *GuardService) ListRequestsForWorkspace(submitter string, isAdmin bool, workspaceID string) []*RepositoryRequest {
	s.mu.RLock()
	defer s.mu.RUnlock()

	var list []*RepositoryRequest
	for _, req := range s.requests {
		if (isAdmin || req.Submitter == submitter) && (workspaceID == "" || req.TargetWorkspace == workspaceID) {
			list = append(list, cloneRequest(req))
		}
	}
	sort.Slice(list, func(i, j int) bool {
		if list[i].SubmittedAt.Equal(list[j].SubmittedAt) {
			return list[i].ID > list[j].ID
		}
		return list[i].SubmittedAt.After(list[j].SubmittedAt)
	})
	return list
}

// GetRequest trả về thông tin chi tiết một yêu cầu
func (s *GuardService) GetRequest(id string) (*RepositoryRequest, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	req, ok := s.requests[id]
	return cloneRequest(req), ok
}

// GetAuditEvents trả về các sự kiện gần đây
func (s *GuardService) GetAuditEvents(limit int) []AuditLogEvent {
	return s.audit.GetRecentEvents(limit)
}
