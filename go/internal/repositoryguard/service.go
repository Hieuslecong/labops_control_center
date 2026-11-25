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

var ErrScanCapacity = errors.New("repository guard is busy; retry later")

const maxConcurrentScans = 2

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
	scanSlots             chan struct{}
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
		scanSlots:     make(chan struct{}, maxConcurrentScans),
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

type SubmitOptions struct {
	Source                string
	LocalRepoRelPath      string
	VerifiedWorkspaceRoot string
	Ref                   string
}

func reportHasSecurityConcerns(report *ScanReport) bool {
	if report == nil {
		return true
	}
	cov := report.Coverage
	return report.SecretsCount > 0 ||
		report.Vulnerabilities.Critical > 0 ||
		report.Vulnerabilities.High > 0 ||
		report.MisconfigHighCount > 0 ||
		cov.Submodules != "NOT_PRESENT" ||
		cov.LFS != "NOT_PRESENT" ||
		cov.OverallCoverage != "POLICY_COMPLETE"
}

func deriveReviewStatus(verdict ScanState, report *ScanReport) string {
	switch verdict {
	case StateRejected:
		return ReviewStatusRejected
	case StateReviewRequired:
		return ReviewStatusReviewRequired
	default:
		if reportHasSecurityConcerns(report) {
			return ReviewStatusReviewRequired
		}
		return ReviewStatusClean
	}
}

// SubmitRequest tiếp nhận yêu cầu clone repo từ Researcher hoặc Admin.
func (s *GuardService) SubmitRequest(ctx context.Context, rawURL, ref, submitter, targetWorkspace string) (*RepositoryRequest, error) {
	return s.SubmitRequestWithOptions(ctx, rawURL, ref, submitter, targetWorkspace, SubmitOptions{})
}

// SubmitRequestWithOptions tiếp nhận yêu cầu kiểm duyệt với tùy chọn hậu kiểm từ workspace local.
func (s *GuardService) SubmitRequestWithOptions(ctx context.Context, rawURL, ref, submitter, targetWorkspace string, opts SubmitOptions) (*RepositoryRequest, error) {
	submitter = strings.TrimSpace(submitter)
	if submitter == "" {
		return nil, errors.New("submitter identity is required")
	}

	canonicalURL, owner, repo, err := s.validateRepoInput(rawURL)
	if err != nil {
		return nil, fmt.Errorf("URL không hợp lệ: %w", err)
	}

	source := strings.TrimSpace(opts.Source)
	if source != SourceWorkspaceClone {
		source = SourceGateway
	}
	cloneStatus := CloneStatusCloning
	reviewStatus := ReviewStatusNotStarted
	if source == SourceWorkspaceClone {
		cloneStatus = CloneStatusCloned
		reviewStatus = ReviewStatusScanning
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
		Source:          source,
		CloneStatus:     cloneStatus,
		ReviewStatus:    reviewStatus,
		LocalRepoPath:   strings.TrimSpace(opts.LocalRepoRelPath),
		SubmittedAt:     now,
		UpdatedAt:       now,
		State:           StateRequested,
		PolicyVersion:   CurrentPolicyVersion,
	}

	s.mu.Lock()
	if strings.HasPrefix(targetWorkspace, "managed:") {
		for _, prior := range s.requests {
			if prior.Submitter != submitter || prior.TargetWorkspace != targetWorkspace ||
				prior.CanonicalRepo != canonicalURL || prior.RequestedRef != ref ||
				(prior.ExpiresAt != nil && !now.Before(*prior.ExpiresAt)) {
				continue
			}
			switch prior.State {
			case StateFailed, StateExpired, StateDelivered, StateRevoked:
				continue
			default:
				reused := cloneRequest(prior)
				s.mu.Unlock()
				return reused, nil
			}
		}
	}
	select {
	case s.scanSlots <- struct{}{}:
	default:
		s.mu.Unlock()
		return nil, ErrScanCapacity
	}
	s.requests[reqID] = req
	if saveErr := s.saveRequestsLocked(); saveErr != nil {
		<-s.scanSlots
		delete(s.requests, reqID)
		s.mu.Unlock()
		return nil, fmt.Errorf("không thể lưu trạng thái yêu cầu: %w", saveErr)
	}
	snap := cloneRequest(req)
	s.mu.Unlock()

	s.audit.LogEvent(submitter, "REPO_REQUEST_SUBMITTED", "REPOSITORY", canonicalURL, "SUCCESS",
		fmt.Sprintf("Yêu cầu kiểm duyệt repo %s (ref: %s, source: %s)", canonicalURL, ref, source))

	go func() {
		defer func() { <-s.scanSlots }()
		s.processRequestWithOptions(context.Background(), reqID, opts)
	}()

	return snap, nil
}

// RescanRequest chạy lại quy trình quét bảo mật cho yêu cầu đang ở trạng thái FAILED hoặc SCAN_FAILED.
func (s *GuardService) RescanRequest(ctx context.Context, reqID string, opts SubmitOptions) (*RepositoryRequest, error) {
	s.mu.Lock()
	req, ok := s.requests[reqID]
	if !ok || req == nil {
		s.mu.Unlock()
		return nil, errors.New("không tìm thấy yêu cầu")
	}
	if req.State != StateFailed && req.ReviewStatus != ReviewStatusScanFailed {
		curr := req.State
		s.mu.Unlock()
		return nil, fmt.Errorf("INVALID_STATE_TRANSITION: chỉ có thể quét lại yêu cầu đang ở trạng thái FAILED/SCAN_FAILED (hiện tại: %s)", curr)
	}
	select {
	case s.scanSlots <- struct{}{}:
	default:
		s.mu.Unlock()
		return nil, ErrScanCapacity
	}

	source := strings.TrimSpace(opts.Source)
	if source == "" {
		source = strings.TrimSpace(req.Source)
	}
	if source != SourceWorkspaceClone {
		source = SourceGateway
	}
	relPath := strings.TrimSpace(opts.LocalRepoRelPath)
	if relPath == "" {
		relPath = strings.TrimSpace(req.LocalRepoPath)
	}
	opts.Source = source
	opts.LocalRepoRelPath = relPath

	if newRef := strings.TrimSpace(opts.Ref); newRef != "" {
		req.RequestedRef = newRef
		if sha40Regex.MatchString(newRef) {
			req.CommitSHA = strings.ToLower(newRef)
		}
	}

	req.Source = source
	req.LocalRepoPath = relPath
	if source == SourceWorkspaceClone {
		req.CloneStatus = CloneStatusCloned
	} else {
		req.CloneStatus = CloneStatusCloning
	}
	req.ReviewStatus = ReviewStatusScanning
	req.State = StateRequested
	req.Error = ""
	req.Report = nil
	req.Decision = nil
	req.UpdatedAt = time.Now()
	if saveErr := s.saveRequestsLocked(); saveErr != nil {
		<-s.scanSlots
		s.mu.Unlock()
		return nil, fmt.Errorf("không thể lưu trạng thái quét lại: %w", saveErr)
	}
	snap := cloneRequest(req)
	canonicalRepo := req.CanonicalRepo
	submitter := req.Submitter
	s.mu.Unlock()

	s.audit.LogEvent(submitter, "REPO_RESCAN_REQUESTED", "REPOSITORY", canonicalRepo, "SUCCESS",
		fmt.Sprintf("Yêu cầu quét lại %s (%s)", reqID, source))

	go func() {
		defer func() { <-s.scanSlots }()
		s.processRequestWithOptions(context.Background(), reqID, opts)
	}()

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
	s.processRequestWithOptions(ctx, reqID, SubmitOptions{})
}

func (s *GuardService) processRequestWithOptions(ctx context.Context, reqID string, opts SubmitOptions) {
	isWorkspaceClone := opts.Source == SourceWorkspaceClone
	s.updateRequest(reqID, func(r *RepositoryRequest) {
		r.State = StateResolvingCommit
		if isWorkspaceClone {
			r.CloneStatus = CloneStatusCloned
			r.ReviewStatus = ReviewStatusScanning
		} else {
			r.CloneStatus = CloneStatusCloning
		}
	})

	canonicalRepo, requestedRef, ok := s.getRequestMeta(reqID)
	if !ok {
		return
	}

	commitSHA, checkoutRef, err := resolveCheckoutRef(ctx, canonicalRepo, requestedRef)
	if err != nil {
		s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateFailed
			r.Error = fmt.Sprintf("Lỗi phân giải commit SHA: %v", err)
			if isWorkspaceClone {
				r.CloneStatus = CloneStatusCloned
				r.ReviewStatus = ReviewStatusScanFailed
			} else {
				r.CloneStatus = CloneStatusFailed
				r.ReviewStatus = ReviewStatusNotStarted
			}
		})
		s.audit.LogEvent("SYSTEM", "COMMIT_RESOLUTION_FAILED", "REPOSITORY", canonicalRepo, "FAILED", err.Error())
		return
	}

	s.updateRequest(reqID, func(r *RepositoryRequest) {
		r.CommitSHA = commitSHA
		r.CheckoutRef = checkoutRef
		r.State = StateCheckingCache
	})

	// 2. Kiểm tra Approved Cache (chỉ áp dụng cho gateway clone; workspace_clone quét riêng trên workspace, không chia sẻ private cache)
	if !isWorkspaceClone {
		if entry, hit := s.store.GetApproved(canonicalRepo, commitSHA); hit {
			expires := entry.ApprovedAt.Add(30 * 24 * time.Hour)
			s.updateRequest(reqID, func(r *RepositoryRequest) {
				r.State = StateAutoApproved
				r.CloneStatus = CloneStatusCloned
				r.ReviewStatus = ReviewStatusClean
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
	}

	s.mu.RLock()
	var submitter string
	if reqItem, exists := s.requests[reqID]; exists && reqItem != nil {
		submitter = reqItem.Submitter
	}
	priorDecision := s.findPriorAdminApprovalLocked(canonicalRepo, submitter)
	s.mu.RUnlock()

	// 3. Tải về vùng Quarantine độc lập (ưu tiên trích xuất cục bộ an toàn cho workspace_clone)
	s.updateRequest(reqID, func(r *RepositoryRequest) { r.State = StateFetching })
	var qw *QuarantineWorkspace
	if isWorkspaceClone && strings.TrimSpace(opts.VerifiedWorkspaceRoot) != "" && strings.TrimSpace(opts.LocalRepoRelPath) != "" {
		qw, err = PrepareQuarantineFromLocalGit(ctx, s.quarantineDir, reqID, opts.VerifiedWorkspaceRoot, opts.LocalRepoRelPath, canonicalRepo, commitSHA)
	} else {
		qw, err = PrepareQuarantineWithCanonical(ctx, s.quarantineDir, reqID, canonicalRepo, canonicalRepo, commitSHA)
	}
	if err != nil {
		if isWorkspaceClone && priorDecision != nil {
			decCopy := *priorDecision
			s.updateRequest(reqID, func(r *RepositoryRequest) {
				r.State = StateManuallyApproved
				r.CloneStatus = CloneStatusCloned
				r.ReviewStatus = ReviewStatusApproved
				r.Decision = &decCopy
				r.Error = ""
			})
			s.audit.LogEvent("SYSTEM", "TRUSTED_REPO_INHERITED", "REPOSITORY", canonicalRepo, "SUCCESS",
				fmt.Sprintf("Kế thừa phê duyệt của Admin %s cho repo đã tin cậy (bỏ qua lỗi fetch)", decCopy.Reviewer))
			return
		}
		s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateFailed
			r.Error = fmt.Sprintf("Lỗi tải về quarantine: %v", err)
			if isWorkspaceClone {
				r.CloneStatus = CloneStatusCloned
				r.ReviewStatus = ReviewStatusScanFailed
			} else {
				r.CloneStatus = CloneStatusFailed
				r.ReviewStatus = ReviewStatusNotStarted
			}
		})
		s.audit.LogEvent("SYSTEM", "QUARANTINE_FETCH_FAILED", "REPOSITORY", canonicalRepo, "FAILED", err.Error())
		return
	}

	// 4. Quét bảo mật
	s.updateRequest(reqID, func(r *RepositoryRequest) {
		r.State = StateScanning
		r.ReviewStatus = ReviewStatusScanning
	})
	s.mu.RLock()
	scanner := s.scanFn
	s.mu.RUnlock()
	if scanner == nil {
		scanner = RunTrivyScan
	}

	report, err := scanner(ctx, qw.SourceDir)
	if err != nil {
		_ = qw.PurgeAll()
		if isWorkspaceClone && priorDecision != nil {
			decCopy := *priorDecision
			s.updateRequest(reqID, func(r *RepositoryRequest) {
				r.State = StateManuallyApproved
				r.CloneStatus = CloneStatusCloned
				r.ReviewStatus = ReviewStatusApproved
				r.Decision = &decCopy
				r.Error = ""
			})
			s.audit.LogEvent("SYSTEM", "TRUSTED_REPO_INHERITED", "REPOSITORY", canonicalRepo, "SUCCESS",
				fmt.Sprintf("Kế thừa phê duyệt của Admin %s cho repo đã tin cậy (bỏ qua lỗi scan)", decCopy.Reviewer))
			return
		}
		s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateFailed
			r.Error = fmt.Sprintf("Lỗi tiến trình quét bảo mật: %v", err)
			r.ReviewStatus = ReviewStatusScanFailed
			if isWorkspaceClone {
				r.CloneStatus = CloneStatusCloned
			} else {
				r.CloneStatus = CloneStatusFailed
			}
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
	reviewStatus := deriveReviewStatus(verdict, report)

	artifactDigest := qw.DigestSHA256
	artifactSize := qw.TotalSize
	provenance := fmt.Sprintf("quarantine:%s", qw.DigestSHA256)
	var expiresAt *time.Time

	if isWorkspaceClone {
		// Không đưa mã nguồn từ workspace vào kho cache dùng chung (bảo vệ private repo giữa các thành viên)
		_ = qw.PurgeAll()
		provenance = fmt.Sprintf("workspace_local:%s@%s", commitSHA, qw.DigestSHA256)
		var inheritedDecision *DecisionRecord
		if priorDecision != nil {
			verdict = StateManuallyApproved
			reviewStatus = ReviewStatusApproved
			decCopy := *priorDecision
			inheritedDecision = &decCopy
		}
		s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = verdict
			r.CloneStatus = CloneStatusCloned
			r.ReviewStatus = reviewStatus
			r.Report = report
			r.Decision = inheritedDecision
			r.ArtifactDigest = artifactDigest
			r.ArtifactSize = artifactSize
			r.ScannerVersion = report.ScannerVersion
			r.DBVersion = report.DBVersion
			r.ArtifactProvenance = provenance
			r.Error = ""
		})
		s.audit.LogEvent("BOT_SECURITY", string(verdict), "COMMIT", commitSHA, "SUCCESS",
			fmt.Sprintf("Kết quả kiểm duyệt hậu kiểm cho %s: %s (review_status=%s)", canonicalRepo, verdict, reviewStatus))
		return
	}

	cloneStatus := CloneStatusPending
	switch verdict {
	case StateAutoApproved:
		cloneStatus = CloneStatusCloned
		entry, errStore := s.store.StoreApproved(canonicalRepo, commitSHA, report.ScannerVersion, report.DBVersion, "BOT_AUTO_APPROVAL", qw)
		_ = qw.PurgeAll()
		if errStore != nil {
			s.updateRequest(reqID, func(r *RepositoryRequest) {
				r.State = StateFailed
				r.CloneStatus = CloneStatusFailed
				r.ReviewStatus = ReviewStatusScanFailed
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
		cloneStatus = CloneStatusPending
		// Xóa mã nguồn bung thô, giữ lại archive 0440 trong quarantine chờ Admin phê duyệt
		_ = qw.Cleanup()

	default:
		if verdict == StateRejected {
			cloneStatus = CloneStatusBlocked
		} else {
			cloneStatus = CloneStatusFailed
		}
		_ = qw.PurgeAll()
	}

	s.updateRequest(reqID, func(r *RepositoryRequest) {
		r.State = verdict
		r.CloneStatus = cloneStatus
		r.ReviewStatus = reviewStatus
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

func (s *GuardService) findPriorAdminApprovalLocked(canonicalRepo, submitter string) *DecisionRecord {
	normRepo := strings.ToLower(strings.TrimSpace(canonicalRepo))
	normSub := strings.ToLower(strings.TrimSpace(submitter))
	if normRepo == "" {
		return nil
	}
	var fallback *DecisionRecord
	for _, r := range s.requests {
		if r == nil || !strings.EqualFold(strings.TrimSpace(r.CanonicalRepo), normRepo) {
			continue
		}
		if r.State != StateManuallyApproved && r.ReviewStatus != ReviewStatusApproved {
			continue
		}
		if r.Decision == nil || (r.Decision.Verdict != StateManuallyApproved && r.Decision.Verdict != StateApproved) {
			continue
		}
		if normSub != "" && strings.EqualFold(strings.TrimSpace(r.Submitter), normSub) {
			return r.Decision
		}
		if fallback == nil {
			fallback = r.Decision
		}
	}
	return fallback
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
	isPostCloneReview := req.Decision == nil &&
		(req.State == StateAutoApproved || req.State == StateDelivered) &&
		(req.ReviewStatus == ReviewStatusReviewRequired || reportHasSecurityConcerns(req.Report))
	archiveFile := filepath.Join(s.quarantineDir, reqID, "source.tar.gz")
	_, archiveStatErr := os.Lstat(archiveFile)
	hasQuarantineArchive := archiveStatErr == nil
	isDirectStatusDecision := req.Source == SourceWorkspaceClone ||
		req.State == StateFailed ||
		req.ReviewStatus == ReviewStatusScanFailed ||
		(req.State == StateReviewRequired && !hasQuarantineArchive)
	if req.State != StateReviewRequired && !isPostCloneReview && !isDirectStatusDecision {
		currentState := req.State
		s.mu.Unlock()
		return nil, fmt.Errorf("INVALID_STATE_TRANSITION: yêu cầu đang ở trạng thái %s, chỉ có thể quyết định khi là REVIEW_REQUIRED hoặc FAILED", currentState)
	}

	now := time.Now()
	if isPostCloneReview || isDirectStatusDecision {
		verdictState := StateManuallyApproved
		reviewStatus := ReviewStatusApproved
		if decision == "REJECTED" {
			verdictState = StateRejected
			reviewStatus = ReviewStatusRejected
		}
		req.State = verdictState
		req.ReviewStatus = reviewStatus
		if decision == "APPROVED" {
			req.CloneStatus = CloneStatusCloned
			req.Error = ""
		} else if req.Source == SourceWorkspaceClone {
			req.CloneStatus = CloneStatusCloned
		} else if req.CloneStatus == "" {
			req.CloneStatus = CloneStatusBlocked
		}
		req.UpdatedAt = now
		req.Decision = &DecisionRecord{
			Verdict:    verdictState,
			Reviewer:   reviewer,
			ReviewedAt: now,
			Reason:     reason,
		}
		_ = s.saveRequestsLocked()
		snap := cloneRequest(req)
		s.mu.Unlock()

		_ = os.RemoveAll(filepath.Join(s.quarantineDir, reqID))
		s.audit.LogEvent(reviewer, fmt.Sprintf("ADMIN_POST_CLONE_%s", decision), "REQUEST", reqID, "SUCCESS",
			fmt.Sprintf("Admin %s đã %s kiểm duyệt repo (Lý do: %s)", reviewer, decision, reason))
		return snap, nil
	}

	if decision == "REJECTED" {
		req.State = StateRejected
		req.CloneStatus = CloneStatusBlocked
		req.ReviewStatus = ReviewStatusRejected
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
		r.CloneStatus = CloneStatusCloned
		r.ReviewStatus = ReviewStatusApproved
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
	return s.deliverAuthorized(reqID, actor, isAdmin, destDir, "", nil)
}

func (s *GuardService) deliverAuthorized(reqID, actor string, isAdmin bool, destDir, workspaceID string, workspace *WorkspaceDelivery) error {
	s.mu.Lock()
	req, ok := s.requests[reqID]
	if !ok || req == nil {
		s.mu.Unlock()
		return errors.New("không tìm thấy yêu cầu")
	}

	if (workspace != nil && (req.TargetWorkspace != "managed:"+workspaceID || req.Submitter != actor)) ||
		(workspace == nil && strings.HasPrefix(req.TargetWorkspace, "managed:")) ||
		(!isAdmin && (strings.TrimSpace(actor) == "" || req.Submitter != actor)) {
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
	checkoutRef := req.CheckoutRef
	approvedDigest := req.ArtifactDigest
	allowedRoots := append([]string(nil), s.allowedWorkspaceRoots...)
	s.mu.Unlock()

	entry, found := s.store.GetApproved(canonicalRepo, commitSHA)
	if !found || (workspace != nil && (entry.ArtifactDigest != approvedDigest || !sha40Regex.MatchString(commitSHA))) {
		err := errors.New("không tìm thấy gói artifact khớp với phê duyệt")
		s.updateRequest(reqID, func(r *RepositoryRequest) {
			r.State = StateDeliveryFailed
			r.DeliveryStatus = "FAILED"
			r.Error = err.Error()
		})
		s.audit.LogEvent(actor, "DELIVERY_FAILED", "WORKSPACE", destDir, "FAILED", err.Error())
		return err
	}

	var deliveryErr error
	if workspace != nil {
		deliveryErr = deliverToWorkspace(context.Background(), entry.ArtifactPath, entry.ArtifactDigest, commitSHA, checkoutRef, canonicalRepo, destDir, *workspace)
	} else {
		deliveryErr = DeliverArtifactStrict(context.Background(), entry.ArtifactPath, entry.ArtifactDigest, commitSHA, destDir, allowedRoots)
	}
	if err := deliveryErr; err != nil {
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
