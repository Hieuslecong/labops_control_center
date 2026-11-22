package repositoryguard

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// 1. Test URL Validation & SSRF Prevention
func TestNormalizeAndValidateURL(t *testing.T) {
	tests := []struct {
		name      string
		rawURL    string
		expectErr bool
		expected  string
	}{
		{
			name:      "Valid GitHub HTTPS",
			rawURL:    "https://github.com/torvalds/linux",
			expectErr: false,
			expected:  "https://github.com/torvalds/linux",
		},
		{
			name:      "Valid GitHub with .git",
			rawURL:    "https://github.com/gin-gonic/gin.git",
			expectErr: false,
			expected:  "https://github.com/gin-gonic/gin",
		},
		{
			name:      "Disallow HTTP",
			rawURL:    "http://github.com/owner/repo",
			expectErr: true,
		},
		{
			name:      "Disallow Credentials in URL",
			rawURL:    "https://user:password@github.com/owner/repo",
			expectErr: true,
		},
		{
			name:      "Disallow Localhost SSRF",
			rawURL:    "https://localhost/owner/repo",
			expectErr: true,
		},
		{
			name:      "Disallow Internal IP SSRF",
			rawURL:    "https://127.0.0.1/owner/repo",
			expectErr: true,
		},
		{
			name:      "Disallow Arbitrary Scheme",
			rawURL:    "file:///etc/passwd",
			expectErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			canonical, _, _, err := NormalizeAndValidateURL(tt.rawURL)
			if (err != nil) != tt.expectErr {
				t.Fatalf("NormalizeAndValidateURL(%q) err = %v, expectErr = %v", tt.rawURL, err, tt.expectErr)
			}
			if !tt.expectErr && canonical != tt.expected {
				t.Fatalf("got canonical %q, want %q", canonical, tt.expected)
			}
		})
	}
}

// 2. Test Policy Engine State Machine & Fail-Closed Behavior
func TestEvaluatePolicy(t *testing.T) {
	// A. Clean report -> AUTO_APPROVED
	cleanReport := &ScanReport{
		Coverage: ScanCoverage{
			VulnerabilityScan:    "COMPLETED",
			SecretScan:           "COMPLETED",
			MisconfigurationScan: "COMPLETED",
			Submodules:           "NOT_PRESENT",
			LFS:                  "NOT_PRESENT",
			OverallCoverage:      "POLICY_COMPLETE",
		},
		Vulnerabilities:    VulnerabilitySummary{Critical: 0, High: 0, Medium: 1, Low: 2},
		SecretsCount:       0,
		MisconfigHighCount: 0,
	}
	verdict, _ := EvaluatePolicy(cleanReport)
	if verdict != StateAutoApproved {
		t.Fatalf("Clean report expected AUTO_APPROVED, got %s", verdict)
	}

	// A2. Fail-Closed when submodules or LFS present or unverified
	subReport := *cleanReport
	subReport.Coverage.Submodules = "PRESENT_UNFETCHED"
	if v, _ := EvaluatePolicy(&subReport); v != StateReviewRequired {
		t.Fatalf("Submodules present expected REVIEW_REQUIRED, got %s", v)
	}
	lfsReport := *cleanReport
	lfsReport.Coverage.LFS = "PRESENT_UNFETCHED"
	if v, _ := EvaluatePolicy(&lfsReport); v != StateReviewRequired {
		t.Fatalf("LFS present expected REVIEW_REQUIRED, got %s", v)
	}
	emptyAuxReport := *cleanReport
	emptyAuxReport.Coverage.Submodules = ""
	if v, _ := EvaluatePolicy(&emptyAuxReport); v != StateReviewRequired {
		t.Fatalf("Uninspected empty submodules expected REVIEW_REQUIRED, got %s", v)
	}

	// B. Leaked Secret -> REVIEW_REQUIRED (cảnh báo cao chờ Admin xác minh false-positive hay lộ khóa thật)
	secretReport := &ScanReport{
		Coverage: ScanCoverage{
			OverallCoverage: "POLICY_COMPLETE",
		},
		SecretsCount: 1,
	}
	verdictSec, reasonsSec := EvaluatePolicy(secretReport)
	if verdictSec != StateReviewRequired {
		t.Fatalf("Secret report expected REVIEW_REQUIRED, got %s", verdictSec)
	}
	if len(reasonsSec) == 0 {
		t.Fatalf("Secret report expected non-empty policy reasons")
	}

	// C. Critical Vulnerability -> REVIEW_REQUIRED (cảnh báo cao chờ Admin đánh giá)
	critReport := &ScanReport{
		Coverage: ScanCoverage{
			OverallCoverage: "POLICY_COMPLETE",
		},
		Vulnerabilities: VulnerabilitySummary{Critical: 1},
	}
	verdictCrit, reasonsCrit := EvaluatePolicy(critReport)
	if verdictCrit != StateReviewRequired {
		t.Fatalf("Critical report expected REVIEW_REQUIRED, got %s", verdictCrit)
	}
	if len(reasonsCrit) == 0 {
		t.Fatalf("Critical report expected non-empty policy reasons")
	}

	// D. High Vulnerability -> REVIEW_REQUIRED
	highReport := &ScanReport{
		Coverage: ScanCoverage{
			OverallCoverage: "POLICY_COMPLETE",
		},
		Vulnerabilities: VulnerabilitySummary{High: 2},
	}
	verdictHigh, _ := EvaluatePolicy(highReport)
	if verdictHigh != StateReviewRequired {
		t.Fatalf("High vuln report expected REVIEW_REQUIRED, got %s", verdictHigh)
	}

	// E. Fail-Closed: Incomplete Scan Coverage -> REVIEW_REQUIRED (Never PASSED)
	incompleteReport := &ScanReport{
		Coverage: ScanCoverage{
			VulnerabilityScan: "FAILED",
			OverallCoverage:   "INCOMPLETE",
		},
		Vulnerabilities: VulnerabilitySummary{Critical: 0, High: 0},
		SecretsCount:    0,
	}
	verdictInc, _ := EvaluatePolicy(incompleteReport)
	if verdictInc != StateReviewRequired {
		t.Fatalf("Incomplete coverage report expected REVIEW_REQUIRED, got %s", verdictInc)
	}

	// F. Permissive / Audit-Only Mode (Mức 3): Always AUTO_APPROVED regardless of secrets/CVEs
	t.Setenv("LABOPS_REPO_PERMISSIVE_MODE", "true")
	verdictPerm, reasonsPerm := EvaluatePolicy(secretReport)
	if verdictPerm != StateAutoApproved {
		t.Fatalf("Permissive mode expected AUTO_APPROVED, got %s", verdictPerm)
	}
	foundPermissiveReason := false
	for _, r := range reasonsPerm {
		if strings.Contains(r, "Chế độ Giám sát Toàn diện") {
			foundPermissiveReason = true
			break
		}
	}
	if !foundPermissiveReason {
		t.Fatalf("Permissive mode expected explanation in reasons, got: %v", reasonsPerm)
	}
}

// 3. Test Approved Store, Cache Hit, and Content Addressing
func TestApprovedArtifactStore(t *testing.T) {
	tempDir, err := os.MkdirTemp("", "labops-approved-store-test-*")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(tempDir)

	store, err := NewApprovedArtifactStore(tempDir)
	if err != nil {
		t.Fatal(err)
	}

	// Tạo archive giả lập
	fakeTar := filepath.Join(tempDir, "sample.tar.gz")
	content := []byte("package main\nfunc main() {}\n")

	f, err := os.Create(fakeTar)
	if err != nil {
		t.Fatal(err)
	}
	gw := gzip.NewWriter(f)
	tw := tar.NewWriter(gw)
	_ = tw.WriteHeader(&tar.Header{Name: "main.go", Mode: 0640, Size: int64(len(content))})
	_, _ = tw.Write(content)
	_ = tw.Close()
	_ = gw.Close()
	_ = f.Close()

	digest, err := HashFileSHA256(fakeTar)
	if err != nil {
		t.Fatalf("HashFileSHA256 failed: %v", err)
	}

	// Negative check: mismatched digest must be rejected by StoreApproved
	badQW := &QuarantineWorkspace{
		ArchiveFile:  fakeTar,
		DigestSHA256: strings.Repeat("a", 64),
	}
	if _, badErr := store.StoreApproved("https://github.com/org/repo", "1111222233334444555566667777888899990000", "0.76.2", "2026-11-10", "BOT", badQW); badErr == nil {
		t.Fatal("StoreApproved MUST fail when DigestSHA256 mismatches actual archive file")
	}

	qw := &QuarantineWorkspace{
		ArchiveFile:  fakeTar,
		DigestSHA256: digest,
	}

	// Store
	entry, err := store.StoreApproved("https://github.com/org/repo", "1111222233334444555566667777888899990000", "0.76.2", "2026-11-10", "BOT", qw)
	if err != nil {
		t.Fatalf("StoreApproved failed: %v", err)
	}

	if entry.ArtifactDigest != digest {
		t.Fatalf("Expected digest %s, got %s", digest, entry.ArtifactDigest)
	}

	// Get approved
	cached, hit := store.GetApproved("https://github.com/org/repo", "1111222233334444555566667777888899990000")
	if !hit || cached == nil {
		t.Fatal("Expected cache hit for approved commit")
	}

	// Cache miss for different commit
	_, hitMiss := store.GetApproved("https://github.com/org/repo", "9999999999999999999999999999999999999999")
	if hitMiss {
		t.Fatal("Expected cache miss for unknown commit")
	}
}

// 4. Test Delivery & TOCTOU Integrity Protection
func TestDeliverArtifact_IntegrityCheck(t *testing.T) {
	tempDir, err := os.MkdirTemp("", "labops-deliver-test-*")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(tempDir)

	archivePath := filepath.Join(tempDir, "valid.tar.gz")
	destDir := filepath.Join(tempDir, "workspace_dest")

	content := []byte("print('hello labops')")
	f, _ := os.Create(archivePath)
	gw := gzip.NewWriter(f)
	tw := tar.NewWriter(gw)
	_ = tw.WriteHeader(&tar.Header{Name: "app.py", Mode: 0644, Size: int64(len(content))})
	_, _ = tw.Write(content)
	_ = tw.Close()
	_ = gw.Close()
	_ = f.Close()

	// Tính digest chính xác
	rf, _ := os.Open(archivePath)
	h := sha256.New()
	_, _ = io.Copy(h, rf)
	rf.Close()
	validDigest := hex.EncodeToString(h.Sum(nil))

	// Deliver với đúng digest
	if err := DeliverArtifact(archivePath, validDigest, destDir); err != nil {
		t.Fatalf("DeliverArtifact with valid digest failed: %v", err)
	}

	// Kiểm tra tệp đã giải nén
	extractedFile := filepath.Join(destDir, "app.py")
	if data, err := os.ReadFile(extractedFile); err != nil || string(data) != string(content) {
		t.Fatalf("Extracted file mismatch or missing: %v", err)
	}

	// Deliver với sai digest (TOCTOU simulation) -> Bắt buộc thất bại
	tamperedDigest := "0000000000000000000000000000000000000000000000000000000000000000"
	tamperedDest := filepath.Join(tempDir, "tampered_dest")
	if err := DeliverArtifact(archivePath, tamperedDigest, tamperedDest); err == nil {
		t.Fatal("DeliverArtifact with mismatched digest MUST fail for integrity check!")
	}
}

// 5. Test Audit Logger
func TestAuditLogger(t *testing.T) {
	tempDir, _ := os.MkdirTemp("", "labops-audit-test-*")
	defer os.RemoveAll(tempDir)

	logPath := filepath.Join(tempDir, "audit.jsonl")
	al, err := NewAuditLogger(logPath)
	if err != nil {
		t.Fatal(err)
	}

	al.LogEvent("user1", "SUBMIT", "REPO", "github.com/org/repo", "SUCCESS", "submitted scan")
	al.LogEvent("admin", "APPROVE", "REPO", "github.com/org/repo", "SUCCESS", "approved exception")

	events := al.GetRecentEvents(10)
	if len(events) != 2 {
		t.Fatalf("Expected 2 audit events, got %d", len(events))
	}
	if events[0].Actor != "admin" {
		t.Fatalf("Expected newest event to be admin action, got %s", events[0].Actor)
	}
}

// 6. Test Guard Service Recovery (Durable Queue)
func TestGuardService_CrashRecovery(t *testing.T) {
	tempDir, _ := os.MkdirTemp("", "labops-recovery-test-*")
	defer os.RemoveAll(tempDir)

	// Tạo requests.json với 1 request bị kẹt ở trạng thái SCANNING trước khi service khởi động
	staleReq := RepositoryRequest{
		ID:            "req-stale-01",
		RepoURL:       "https://github.com/test/repo",
		CanonicalRepo: "https://github.com/test/repo",
		CommitSHA:     "1234567890123456789012345678901234567890",
		State:         StateScanning,
		SubmittedAt:   time.Now().Add(-10 * time.Minute),
		UpdatedAt:     time.Now().Add(-10 * time.Minute),
	}

	data := `[` + "\n" + `  {
    "id": "req-stale-01",
    "repo_url": "https://github.com/test/repo",
    "canonical_repo": "https://github.com/test/repo",
    "commit_sha": "1234567890123456789012345678901234567890",
    "state": "SCANNING",
    "policy_version": "labops-repo-policy-2026.11-v2"
  }` + "\n" + `]`
	_ = os.WriteFile(filepath.Join(tempDir, "requests.json"), []byte(data), 0640)

	// Khởi động GuardService
	svc, err := NewGuardService(tempDir)
	if err != nil {
		t.Fatal(err)
	}

	recoveredReq, ok := svc.GetRequest("req-stale-01")
	if !ok {
		t.Fatal("Could not find recovered request")
	}

	if recoveredReq.State != StateFailed {
		t.Fatalf("Expected stale request to be transitioned to FAILED, got %s", recoveredReq.State)
	}
	if recoveredReq.Error == "" {
		t.Fatal("Expected recovery error message explaining interruption")
	}
	_ = staleReq
}

type testTarSpec struct {
	header *tar.Header
	body   []byte
}

func writeCustomTarGz(t *testing.T, outPath string, entries []testTarSpec) string {
	t.Helper()
	f, err := os.Create(outPath)
	if err != nil {
		t.Fatalf("create archive %s: %v", outPath, err)
	}
	gw := gzip.NewWriter(f)
	tw := tar.NewWriter(gw)
	for _, ent := range entries {
		if err := tw.WriteHeader(ent.header); err != nil {
			t.Fatalf("write tar header %s: %v", ent.header.Name, err)
		}
		if len(ent.body) > 0 {
			if _, err := tw.Write(ent.body); err != nil {
				t.Fatalf("write tar body %s: %v", ent.header.Name, err)
			}
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatalf("close tar: %v", err)
	}
	if err := gw.Close(); err != nil {
		t.Fatalf("close gzip: %v", err)
	}
	if err := f.Close(); err != nil {
		t.Fatalf("close file: %v", err)
	}
	digest, err := HashFileSHA256(outPath)
	if err != nil {
		t.Fatalf("hash archive %s: %v", outPath, err)
	}
	return digest
}

// 7. Test Archive Path Depth, Symlink Escape, LimitReader Caps & Destination Confinement
func TestArchiveAndDeliverySecurityHardening(t *testing.T) {
	tempDir := t.TempDir()

	// A. ValidateArchivePath rejects traversal and depth > MaxArchivePathDepth
	deepParts := make([]string, MaxArchivePathDepth+1)
	for i := range deepParts {
		deepParts[i] = "d"
	}
	if _, err := ValidateArchivePath(strings.Join(deepParts, "/")); err == nil {
		t.Fatal("ValidateArchivePath MUST reject paths exceeding MaxArchivePathDepth")
	}
	for _, badPath := range []string{"../etc/passwd", "/etc/shadow", "a\\b", "foo\x00bar"} {
		if _, err := ValidateArchivePath(badPath); err == nil {
			t.Fatalf("ValidateArchivePath MUST reject unsafe path %q", badPath)
		}
	}

	// B. Reject absolute symlink (/etc/passwd) inside archive
	absSymTar := filepath.Join(tempDir, "abs_sym.tar.gz")
	absDigest := writeCustomTarGz(t, absSymTar, []testTarSpec{
		{header: &tar.Header{Name: "leak", Typeflag: tar.TypeSymlink, Linkname: "/etc/passwd", Mode: 0777}},
	})
	if err := DeliverArtifact(absSymTar, absDigest, filepath.Join(tempDir, "out_abs_sym")); err == nil {
		t.Fatal("DeliverArtifact MUST reject absolute symlink pointing to /etc/passwd")
	}

	// C. Reject chained symlink traversal (dirlink -> sub, then write through dirlink/pwned or escaping relative symlink)
	chainTar := filepath.Join(tempDir, "chain_sym.tar.gz")
	chainDigest := writeCustomTarGz(t, chainTar, []testTarSpec{
		{header: &tar.Header{Name: "sub", Typeflag: tar.TypeDir, Mode: 0755}},
		{header: &tar.Header{Name: "dirlink", Typeflag: tar.TypeSymlink, Linkname: "sub", Mode: 0777}},
		{header: &tar.Header{Name: "dirlink/nested.txt", Typeflag: tar.TypeReg, Mode: 0644, Size: 3}, body: []byte("bad")},
	})
	if err := DeliverArtifact(chainTar, chainDigest, filepath.Join(tempDir, "out_chain")); err == nil {
		t.Fatal("DeliverArtifact MUST reject writing through a symlink intermediate directory")
	}

	// D. CopyAndVerifySHA256 rejects archive exceeding maxBytes limit (bomb guard)
	bombTar := filepath.Join(tempDir, "bomb.tar.gz")
	bigBody := bytes.Repeat([]byte("A"), 2048)
	_ = writeCustomTarGz(t, bombTar, []testTarSpec{
		{header: &tar.Header{Name: "big.bin", Typeflag: tar.TypeReg, Mode: 0644, Size: int64(len(bigBody))}, body: bigBody},
	})
	if _, _, err := CopyAndVerifySHA256(bombTar, tempDir, "", 64); err == nil {
		t.Fatal("CopyAndVerifySHA256 MUST fail closed when archive size exceeds maxBytes")
	}

	// E. Reject dangerous destinations, outside allowedRoots, and symlinked destDir
	validTar := filepath.Join(tempDir, "ok.tar.gz")
	okDigest := writeCustomTarGz(t, validTar, []testTarSpec{
		{header: &tar.Header{Name: "README.md", Typeflag: tar.TypeReg, Mode: 0644, Size: 2}, body: []byte("ok")},
	})
	allowedRoot := filepath.Join(tempDir, "workspaces")
	if err := os.MkdirAll(allowedRoot, 0750); err != nil {
		t.Fatal(err)
	}

	for _, badDest := range []string{
		"/etc/labops-evil",
		"/home/alice/.ssh/evil",
		filepath.Join(tempDir, "outside-ws"),
		"relative/path",
	} {
		if err := DeliverArtifactStrict(context.Background(), validTar, okDigest, "", badDest, []string{allowedRoot}); err == nil {
			t.Fatalf("DeliverArtifactStrict MUST reject unsafe destination %q", badDest)
		}
	}

	symlinkDestParent := filepath.Join(allowedRoot, "symparent")
	outsideRealDir := filepath.Join(tempDir, "real_outside")
	_ = os.MkdirAll(outsideRealDir, 0750)
	if err := os.Symlink(outsideRealDir, symlinkDestParent); err != nil {
		t.Fatal(err)
	}
	if err := DeliverArtifactStrict(context.Background(), validTar, okDigest, "", filepath.Join(symlinkDestParent, "target"), []string{allowedRoot}); err == nil {
		t.Fatal("DeliverArtifactStrict MUST reject destination descending through symlink ancestor")
	}

	// F. Missing .labops_bundle must fail when expectedCommitSHA is required
	if err := DeliverArtifactStrict(context.Background(), validTar, okDigest, "1111222233334444555566667777888899990000", filepath.Join(allowedRoot, "clean_dest"), []string{allowedRoot}); err == nil {
		t.Fatal("DeliverArtifactStrict MUST reject delivery when expectedCommitSHA is set but .labops_bundle is absent")
	}
}

// 8. Test State Machine Enforcement, Quarantine Tamper Detection & Crash Recovery
func TestGuardService_StateTransitionsAndTamperDetection(t *testing.T) {
	tempDir := t.TempDir()

	// Corrupt requests.json must fail closed
	corruptDir := filepath.Join(tempDir, "corrupt")
	_ = os.MkdirAll(corruptDir, 0750)
	_ = os.WriteFile(filepath.Join(corruptDir, "requests.json"), []byte("{corrupt"), 0640)
	if _, err := NewGuardService(corruptDir); err == nil {
		t.Fatal("NewGuardService MUST fail closed on corrupt requests.json")
	}

	// Recover REQUESTED and DELIVERING states cleanly
	recDir := filepath.Join(tempDir, "recovery")
	_ = os.MkdirAll(recDir, 0750)
	seedReqs := `[
	  {"id":"req-r1","repo_url":"https://github.com/o/r","canonical_repo":"https://github.com/o/r","commit_sha":"` + strings.Repeat("1", 40) + `","submitted_by":"alice","state":"REQUESTED","policy_version":"` + CurrentPolicyVersion + `"},
	  {"id":"req-d1","repo_url":"https://github.com/o/r","canonical_repo":"https://github.com/o/r","commit_sha":"` + strings.Repeat("2", 40) + `","submitted_by":"alice","state":"DELIVERING","policy_version":"` + CurrentPolicyVersion + `"},
	  {"id":"req-rej","repo_url":"https://github.com/o/r","canonical_repo":"https://github.com/o/r","commit_sha":"` + strings.Repeat("3", 40) + `","submitted_by":"alice","state":"REJECTED","policy_version":"` + CurrentPolicyVersion + `"},
	  {"id":"req-rev","repo_url":"https://github.com/o/r","canonical_repo":"https://github.com/o/r","commit_sha":"` + strings.Repeat("4", 40) + `","submitted_by":"alice","state":"REVIEW_REQUIRED","artifact_digest":"DIGEST_PLACEHOLDER","policy_version":"` + CurrentPolicyVersion + `"}
	]`

	qDir := filepath.Join(recDir, "quarantine", "req-rev")
	_ = os.MkdirAll(qDir, 0750)
	qArchive := filepath.Join(qDir, "source.tar.gz")
	origDigest := writeCustomTarGz(t, qArchive, []testTarSpec{
		{header: &tar.Header{Name: "app.py", Typeflag: tar.TypeReg, Mode: 0644, Size: 5}, body: []byte("x = 1")},
	})
	seedReqs = strings.ReplaceAll(seedReqs, "DIGEST_PLACEHOLDER", origDigest)
	if err := os.WriteFile(filepath.Join(recDir, "requests.json"), []byte(seedReqs), 0640); err != nil {
		t.Fatal(err)
	}

	svc, err := NewGuardService(recDir)
	if err != nil {
		t.Fatalf("NewGuardService recovery failed: %v", err)
	}

	if r1, ok := svc.GetRequest("req-r1"); !ok || r1.State != StateFailed {
		t.Fatalf("Expected REQUESTED to recover to FAILED, got %+v", r1)
	}
	if d1, ok := svc.GetRequest("req-d1"); !ok || d1.State != StateDeliveryFailed {
		t.Fatalf("Expected DELIVERING to recover to DELIVERY_FAILED, got %+v", d1)
	}

	// Reject invalid transition REJECTED -> APPROVED
	if _, err := svc.AdminDecision("req-rej", "APPROVED", "admin-sec", "cannot approve rejected"); err == nil {
		t.Fatal("AdminDecision MUST reject REJECTED -> APPROVED state transition")
	}

	// Tamper quarantine archive before AdminDecision("APPROVED") -> MUST fail & mark FAILED
	if err := os.Chmod(qArchive, 0640); err == nil {
		if err := os.WriteFile(qArchive, []byte("tampered archive bytes"), 0640); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := svc.AdminDecision("req-rev", "APPROVED", "admin-sec", "override"); err == nil {
		t.Fatal("AdminDecision MUST reject tampered quarantine archive")
	}
	if revReq, ok := svc.GetRequest("req-rev"); !ok || revReq.State != StateFailed {
		t.Fatalf("Expected tampered request to end in StateFailed, got %+v", revReq)
	}
	if _, cached := svc.store.GetApproved("https://github.com/o/r", strings.Repeat("4", 40)); cached {
		t.Fatal("Tampered artifact MUST NOT enter ApprovedArtifactStore")
	}
}

// 9. Test HTTPHandler Broker HMAC Verification & RBAC Enforcement
func TestHTTPHandler_HMACAndRBAC(t *testing.T) {
	tempDir := t.TempDir()
	dataDir := filepath.Join(tempDir, "guard")
	wsRoot := filepath.Join(tempDir, "workspaces")
	_ = os.MkdirAll(dataDir, 0750)
	_ = os.MkdirAll(wsRoot, 0750)

	seedReqs := `[
	  {"id":"req-alice","repo_url":"https://github.com/o/a","canonical_repo":"https://github.com/o/a","commit_sha":"` + strings.Repeat("a", 40) + `","submitted_by":"alice","state":"REVIEW_REQUIRED","policy_version":"` + CurrentPolicyVersion + `"},
	  {"id":"req-bob","repo_url":"https://github.com/o/b","canonical_repo":"https://github.com/o/b","commit_sha":"` + strings.Repeat("b", 40) + `","submitted_by":"bob","state":"AUTO_APPROVED","policy_version":"` + CurrentPolicyVersion + `"}
	]`
	if err := os.WriteFile(filepath.Join(dataDir, "requests.json"), []byte(seedReqs), 0640); err != nil {
		t.Fatal(err)
	}

	svc, err := NewGuardService(dataDir)
	if err != nil {
		t.Fatal(err)
	}
	secret := "broker-hmac-secret-test-0123456789abcdef"
	h := NewHTTPHandlerWithConfig(svc, secret, []string{wsRoot})
	mux := http.NewServeMux()
	h.RegisterRoutes(mux)

	now := time.Now().Unix()

	// 1. Missing broker headers -> 401
	reqUnauth := httptest.NewRequest(http.MethodGet, "/api/v1/agent/repositoryguard/requests", nil)
	wUnauth := httptest.NewRecorder()
	mux.ServeHTTP(wUnauth, reqUnauth)
	if wUnauth.Code != http.StatusUnauthorized {
		t.Fatalf("Expected 401 for unauthenticated GET /requests, got %d", wUnauth.Code)
	}

	// 2. Privilege escalation attempt ?is_admin=true by RESEARCHER alice -> receives ONLY alice's request
	reqEsc := httptest.NewRequest(http.MethodGet, "/api/v1/agent/repositoryguard/requests?is_admin=true&submitter=bob", nil)
	ApplyBrokerAuthHeaders(reqEsc, secret, "alice", "RESEARCHER", now)
	wEsc := httptest.NewRecorder()
	mux.ServeHTTP(wEsc, reqEsc)
	if wEsc.Code != http.StatusOK {
		t.Fatalf("Expected 200 for authenticated researcher GET /requests, got %d", wEsc.Code)
	}
	var listed []*RepositoryRequest
	if err := json.Unmarshal(wEsc.Body.Bytes(), &listed); err != nil {
		t.Fatal(err)
	}
	if len(listed) != 1 || listed[0].ID != "req-alice" {
		t.Fatalf("Researcher escalation bypass leaked other users' requests: %+v", listed)
	}

	// 3. Spoofed submitter on POST /requests by researcher -> 403
	reqSpoof := httptest.NewRequest(http.MethodPost, "/api/v1/agent/repositoryguard/requests",
		strings.NewReader(`{"repo_url":"https://github.com/org/repo","ref":"HEAD","submitter":"bob"}`))
	ApplyBrokerAuthHeaders(reqSpoof, secret, "alice", "RESEARCHER", now)
	wSpoof := httptest.NewRecorder()
	mux.ServeHTTP(wSpoof, reqSpoof)
	if wSpoof.Code != http.StatusForbidden {
		t.Fatalf("Expected 403 for submitter spoofing, got %d", wSpoof.Code)
	}

	// 4. Researcher trying Admin decision -> 403 Forbidden
	reqDecRes := httptest.NewRequest(http.MethodPost, "/api/v1/agent/repositoryguard/requests/req-alice/decision",
		strings.NewReader(`{"decision":"APPROVED","reason":"self-approve"}`))
	ApplyBrokerAuthHeaders(reqDecRes, secret, "alice", "RESEARCHER", now)
	wDecRes := httptest.NewRecorder()
	mux.ServeHTTP(wDecRes, reqDecRes)
	if wDecRes.Code != http.StatusForbidden {
		t.Fatalf("Expected 403 when non-admin invokes /decision, got %d", wDecRes.Code)
	}

	// 5. Researcher trying to deliver another user's request -> 403 Forbidden
	reqDelOther := httptest.NewRequest(http.MethodPost, "/api/v1/agent/repositoryguard/deliver",
		strings.NewReader(`{"request_id":"req-bob","target_directory":"`+filepath.Join(wsRoot, "stolen")+`"}`))
	ApplyBrokerAuthHeaders(reqDelOther, secret, "alice", "RESEARCHER", now)
	wDelOther := httptest.NewRecorder()
	mux.ServeHTTP(wDelOther, reqDelOther)
	if wDelOther.Code != http.StatusForbidden {
		t.Fatalf("Expected 403 when non-owner attempts delivery, got %d", wDelOther.Code)
	}

	// 6. Researcher trying GET /audit -> 403 Forbidden
	reqAudRes := httptest.NewRequest(http.MethodGet, "/api/v1/agent/repositoryguard/audit", nil)
	ApplyBrokerAuthHeaders(reqAudRes, secret, "alice", "RESEARCHER", now)
	wAudRes := httptest.NewRecorder()
	mux.ServeHTTP(wAudRes, reqAudRes)
	if wAudRes.Code != http.StatusForbidden {
		t.Fatalf("Expected 403 for researcher GET /audit, got %d", wAudRes.Code)
	}

	// 7. Verified Admin can reject req-alice -> 200 and reviewer forced to verified actor
	reqDecAdm := httptest.NewRequest(http.MethodPost, "/api/v1/agent/repositoryguard/requests/req-alice/decision",
		strings.NewReader(`{"decision":"REJECTED","reviewer":"fake-reviewer","reason":"policy deny"}`))
	ApplyBrokerAuthHeaders(reqDecAdm, secret, "admin-sec", "ADMIN", now)
	wDecAdm := httptest.NewRecorder()
	mux.ServeHTTP(wDecAdm, reqDecAdm)
	if wDecAdm.Code != http.StatusOK {
		t.Fatalf("Expected 200 for Admin decision, got %d (%s)", wDecAdm.Code, wDecAdm.Body.String())
	}
	var updated RepositoryRequest
	if err := json.Unmarshal(wDecAdm.Body.Bytes(), &updated); err != nil {
		t.Fatal(err)
	}
	if updated.State != StateRejected || updated.Decision == nil || updated.Decision.Reviewer != "admin-sec" {
		t.Fatalf("Unexpected AdminDecision result: %+v", updated)
	}
}

// 10. Full E2E Test: Dumb-HTTP Git Server -> Quarantine -> Bundle Seal -> Approved Store -> Real .git Delivery
func TestGuardService_FullGitBundleE2E(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git binary not installed")
	}

	tempDir := t.TempDir()
	srcRepo := filepath.Join(tempDir, "src_repo")
	srvRoot := filepath.Join(tempDir, "srv_root")
	bareRepo := filepath.Join(srvRoot, "org", "demo.git")
	guardDir := filepath.Join(tempDir, "guard_data")
	wsRoot := filepath.Join(tempDir, "workspaces")
	_ = os.MkdirAll(srcRepo, 0750)
	_ = os.MkdirAll(filepath.Dir(bareRepo), 0750)
	_ = os.MkdirAll(wsRoot, 0750)

	runGitTest := func(dir string, args ...string) string {
		t.Helper()
		cmd := exec.Command("git", args...)
		if dir != "" {
			cmd.Dir = dir
		}
		cmd.Env = append(os.Environ(),
			"GIT_TERMINAL_PROMPT=0",
			"GIT_CONFIG_NOSYSTEM=1",
			"GIT_CONFIG_GLOBAL=/dev/null",
		)
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("git %v in %s failed: %v\n%s", args, dir, err, string(out))
		}
		return strings.TrimSpace(string(out))
	}

	runGitTest("", "init", srcRepo)
	runGitTest(srcRepo, "symbolic-ref", "HEAD", "refs/heads/main")
	if err := os.MkdirAll(filepath.Join(srcRepo, "src"), 0750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(srcRepo, "train.py"), []byte("print('hello e2e')\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(srcRepo, "src", "model.py"), []byte("LAYER = 42\n"), 0644); err != nil {
		t.Fatal(err)
	}
	runGitTest(srcRepo, "add", ".")
	runGitTest(srcRepo, "-c", "user.name=LabOps Test", "-c", "user.email=test@lab.local", "commit", "-m", "initial verified commit")
	if err := os.WriteFile(filepath.Join(srcRepo, ".gitattributes"), []byte("src/model.py export-ignore\n"), 0644); err != nil {
		t.Fatal(err)
	}
	runGitTest(srcRepo, "add", ".gitattributes")
	runGitTest(srcRepo, "-c", "user.name=LabOps Test", "-c", "user.email=test@lab.local", "commit", "-m", "add export attribute")

	expectedSHA := runGitTest(srcRepo, "rev-parse", "HEAD")
	runGitTest(srcRepo, "branch", "topics/verified")

	runGitTest("", "clone", "--bare", srcRepo, bareRepo)
	runGitTest("", "--git-dir", bareRepo, "update-server-info")

	gitSrv := httptest.NewServer(http.FileServer(http.Dir(srvRoot)))
	defer gitSrv.Close()

	svc, err := NewGuardService(guardDir)
	if err != nil {
		t.Fatal(err)
	}
	svc.SetAllowLoopbackForTest(true)
	svc.SetWorkspaceRoots([]string{wsRoot})
	svc.SetScanFuncForTest(func(ctx context.Context, sourceDir string) (*ScanReport, error) {
		if _, err := os.Stat(filepath.Join(sourceDir, "src", "model.py")); err != nil {
			return nil, err
		}
		sub, lfs := InspectAuxiliaryFeatures(sourceDir)
		return &ScanReport{
			Coverage: ScanCoverage{
				VulnerabilityScan:    "COMPLETED",
				SecretScan:           "COMPLETED",
				MisconfigurationScan: "COMPLETED",
				Submodules:           sub,
				LFS:                  lfs,
				OverallCoverage:      "POLICY_COMPLETE",
			},
			Vulnerabilities: VulnerabilitySummary{},
			ScannerVersion:  "0.76.2-test",
			DBVersion:       "2026-11-18",
		}, nil
	})

	repoURL := gitSrv.URL + "/org/demo.git"
	req, err := svc.SubmitRequest(context.Background(), repoURL, "HEAD", "alice", "ws-alice")
	if err != nil {
		t.Fatalf("SubmitRequest failed: %v", err)
	}

	var finalReq *RepositoryRequest
	deadline := time.Now().Add(20 * time.Second)
	for time.Now().Before(deadline) {
		curr, ok := svc.GetRequest(req.ID)
		if ok && (curr.State == StateAutoApproved || curr.State == StateFailed || curr.State == StateRejected || curr.State == StateReviewRequired) {
			finalReq = curr
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	if finalReq == nil || finalReq.State != StateAutoApproved {
		t.Fatalf("Expected E2E request to reach StateAutoApproved, got %+v", finalReq)
	}
	if finalReq.CommitSHA != expectedSHA {
		t.Fatalf("CommitSHA mismatch: got %s, want %s", finalReq.CommitSHA, expectedSHA)
	}

	// Deliver to researcher workspace directory
	destDir := filepath.Join(wsRoot, "alice", "demo")
	if err := svc.DeliverAuthorized(finalReq.ID, "alice", false, destDir); err != nil {
		t.Fatalf("DeliverAuthorized E2E failed: %v", err)
	}

	// Verify real materialized Git working tree & remote neutralization
	if inside := runGitTest(destDir, "rev-parse", "--is-inside-work-tree"); inside != "true" {
		t.Fatalf("Expected delivered directory to be git worktree, got %q", inside)
	}
	if head := runGitTest(destDir, "rev-parse", "HEAD"); head != expectedSHA {
		t.Fatalf("Delivered HEAD mismatch: got %s, want %s", head, expectedSHA)
	}
	if statusOut := runGitTest(destDir, "status", "--porcelain"); statusOut != "" {
		t.Fatalf("Delivered git worktree expected clean, got %q", statusOut)
	}
	if originURL := runGitTest(destDir, "remote", "get-url", "origin"); originURL != ReapprovalRequiredOriginURL {
		t.Fatalf("Expected origin pull URL %q, got %q", ReapprovalRequiredOriginURL, originURL)
	}
	if logMsg := runGitTest(destDir, "log", "-1", "--format=%s"); logMsg != "add export attribute" {
		t.Fatalf("Unexpected git log commit message: %q", logMsg)
	}
	if count := runGitTest(destDir, "rev-list", "--count", "HEAD"); count != "2" {
		t.Fatalf("Expected complete fetched history, got %q commits", count)
	}

	managed, err := svc.SubmitRequest(context.Background(), repoURL, "topics/verified", "alice", "managed:ws-alice")
	if err != nil {
		t.Fatal(err)
	}
	retry, err := svc.SubmitRequest(context.Background(), repoURL, "topics/verified", "alice", "managed:ws-alice")
	if err != nil || retry.ID != managed.ID {
		t.Fatalf("Equivalent request was not reused: %+v, %v", retry, err)
	}
	deadline = time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		managed, _ = svc.GetRequest(managed.ID)
		if managed.State == StateAutoApproved || managed.State == StateFailed {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if managed.State != StateAutoApproved || !managed.CacheHit {
		t.Fatalf("Managed request should use the verified commit cache: %+v", managed)
	}
	stageBase := filepath.Join(tempDir, "delivery-staging")
	if err := os.Mkdir(stageBase, 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("LABOPS_REPOSITORY_DELIVERY_STAGING", stageBase)
	workspace := WorkspaceDelivery{Root: filepath.Join(wsRoot, "alice"), UID: os.Geteuid(), GID: os.Getegid()}
	entry, found := svc.store.GetApproved(managed.CanonicalRepo, expectedSHA)
	if !found {
		t.Fatal("approved artifact is missing")
	}
	if err := deliverToWorkspace(context.Background(), entry.ArtifactPath, entry.ArtifactDigest,
		strings.Repeat("1", 40), managed.CheckoutRef, repoURL, "wrong-commit", workspace); err == nil {
		t.Fatal("approved artifact must be rejected when the expected commit differs")
	}
	if _, err := os.Lstat(filepath.Join(workspace.Root, "wrong-commit")); !os.IsNotExist(err) {
		t.Fatalf("mismatched commit left a destination: %v", err)
	}
	if err := svc.deliverAuthorized(managed.ID, "alice", false, "managed", "ws-alice", &workspace); err != nil {
		t.Fatal(err)
	}
	managedDest := filepath.Join(workspace.Root, "managed")
	if branch := runGitTest(managedDest, "branch", "--show-current"); branch != "topics/verified" {
		t.Fatalf("Managed checkout changed the requested branch: %s", branch)
	}
	if upstream := runGitTest(managedDest, "rev-parse", "--abbrev-ref", "@{upstream}"); upstream != "origin/topics/verified" {
		t.Fatalf("Unexpected upstream: %s", upstream)
	}
	if head := runGitTest(managedDest, "rev-parse", "HEAD"); head != expectedSHA {
		t.Fatalf("Managed delivery changed the approved commit: %s", head)
	}

	trainBytes, err := os.ReadFile(filepath.Join(destDir, "train.py"))
	if err != nil || string(trainBytes) != "print('hello e2e')\n" {
		t.Fatalf("Delivered train.py mismatch: err=%v, content=%q", err, string(trainBytes))
	}
	if _, err := os.Stat(filepath.Join(destDir, BundleSubdirName)); !os.IsNotExist(err) {
		t.Fatalf(".labops_bundle staging directory should not be left in delivered worktree")
	}
}

// 11. Test Local Workspace Clone Post-Clone Review, Private Cache Isolation, AdminDecision & Rescan
func TestGuardService_WorkspaceClonePostReviewAndRescan(t *testing.T) {
	tempDir, err := os.MkdirTemp("", "labops-ws-clone-review-*")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(tempDir)

	guardDir := filepath.Join(tempDir, "guard")
	wsRoot := filepath.Join(tempDir, "ws-tranglt")
	repoDir := filepath.Join(wsRoot, "risk-constrained-endpointing")
	if err := os.MkdirAll(repoDir, 0755); err != nil {
		t.Fatal(err)
	}

	runGitLocal := func(dir string, args ...string) string {
		t.Helper()
		cmd := exec.Command("git", args...)
		if dir != "" {
			cmd.Dir = dir
		}
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("git %v failed: %v\n%s", args, err, string(out))
		}
		return strings.TrimSpace(string(out))
	}

	runGitLocal(repoDir, "init")
	if err := os.WriteFile(filepath.Join(repoDir, "app.py"), []byte("API_KEY = 'demo'\n"), 0644); err != nil {
		t.Fatal(err)
	}
	runGitLocal(repoDir, "add", ".")
	runGitLocal(repoDir, "-c", "user.name=Trang", "-c", "user.email=trang@lab.local", "commit", "-m", "private commit")
	commitSHA := runGitLocal(repoDir, "rev-parse", "HEAD")

	// Reject traversal / symlink escape in PrepareQuarantineFromLocalGit
	if _, err := PrepareQuarantineFromLocalGit(context.Background(), filepath.Join(guardDir, "quarantine"), "req-bad", wsRoot, "../escape", "https://github.com/TrangLe1912/risk-constrained-endpointing", commitSHA); err == nil {
		t.Fatal("PrepareQuarantineFromLocalGit MUST reject traversal relative paths")
	}

	t.Setenv("LABOPS_REPO_PERMISSIVE_MODE", "true")
	svc, err := NewGuardService(guardDir)
	if err != nil {
		t.Fatal(err)
	}
	svc.SetScanFuncForTest(func(ctx context.Context, sourceDir string) (*ScanReport, error) {
		if _, err := os.Stat(filepath.Join(sourceDir, "app.py")); err != nil {
			return nil, fmt.Errorf("missing extracted app.py: %w", err)
		}
		return &ScanReport{
			Coverage: ScanCoverage{
				VulnerabilityScan:    "COMPLETED",
				SecretScan:           "COMPLETED",
				MisconfigurationScan: "COMPLETED",
				Submodules:           "NOT_PRESENT",
				LFS:                  "NOT_PRESENT",
				OverallCoverage:      "POLICY_COMPLETE",
			},
			Vulnerabilities: VulnerabilitySummary{High: 1},
			SecretsCount:    1,
			ScannerVersion:  "0.76.2-test",
			Findings: []FindingItem{
				{ID: "generic-api-key", Type: "SECRET", Severity: "HIGH", Title: "API Key", TargetFile: "app.py:1", MaskedValue: "****"},
			},
		}, nil
	})

	req, err := svc.SubmitRequestWithOptions(
		context.Background(),
		"https://github.com/TrangLe1912/risk-constrained-endpointing",
		commitSHA,
		"tranglt",
		"ws-tranglt",
		SubmitOptions{
			Source:                SourceWorkspaceClone,
			LocalRepoRelPath:      "risk-constrained-endpointing",
			VerifiedWorkspaceRoot: wsRoot,
		},
	)
	if err != nil {
		t.Fatalf("SubmitRequestWithOptions failed: %v", err)
	}

	var doneReq *RepositoryRequest
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		curr, ok := svc.GetRequest(req.ID)
		if ok && curr.State == StateAutoApproved && curr.ReviewStatus == ReviewStatusReviewRequired {
			doneReq = curr
			break
		}
		time.Sleep(25 * time.Millisecond)
	}
	if doneReq == nil {
		curr, _ := svc.GetRequest(req.ID)
		t.Fatalf("Expected AUTO_APPROVED + ReviewStatusReviewRequired, got %+v", curr)
	}
	if doneReq.CloneStatus != CloneStatusCloned || doneReq.Source != SourceWorkspaceClone {
		t.Fatalf("Expected CloneStatus=CLONED & Source=workspace_clone, got %+v", doneReq)
	}
	// Private workspace clone must NOT be stored into shared ApprovedArtifactStore
	if _, leaked := svc.store.GetApproved(doneReq.CanonicalRepo, commitSHA); leaked {
		t.Fatal("workspace_clone private repo MUST NOT be cached in shared ApprovedArtifactStore")
	}

	// Post-clone AdminDecision APPROVED preserves CloneStatus=CLONED and marks ReviewStatus=APPROVED
	decided, err := svc.AdminDecision(doneReq.ID, "APPROVED", "admin", "Verified false positive")
	if err != nil {
		t.Fatalf("Post-clone AdminDecision failed: %v", err)
	}
	if decided.ReviewStatus != ReviewStatusApproved || decided.CloneStatus != CloneStatusCloned || decided.State != StateManuallyApproved {
		t.Fatalf("Unexpected post-clone decision result: %+v", decided)
	}

	// RescanRequest recovers a pre-existing FAILED request in-place with local workspace scanning
	svc.mu.Lock()
	svc.requests["req-failed-old"] = &RepositoryRequest{
		ID:            "req-failed-old",
		RepoURL:       "https://github.com/TrangLe1912/risk-constrained-endpointing",
		CanonicalRepo: "https://github.com/TrangLe1912/risk-constrained-endpointing",
		Owner:         "TrangLe1912",
		RepoName:      "risk-constrained-endpointing",
		RequestedRef:  commitSHA,
		CommitSHA:     commitSHA,
		Submitter:     "tranglt",
		SubmittedAt:   time.Now(),
		UpdatedAt:     time.Now(),
		State:         StateFailed,
		Error:         "fatal: could not read Username for 'https://github.com'",
	}
	svc.mu.Unlock()

	rescanned, err := svc.RescanRequest(context.Background(), "req-failed-old", SubmitOptions{
		Source:                SourceWorkspaceClone,
		LocalRepoRelPath:      "risk-constrained-endpointing",
		VerifiedWorkspaceRoot: wsRoot,
	})
	if err != nil {
		t.Fatalf("RescanRequest failed: %v", err)
	}
	if rescanned.CloneStatus != CloneStatusCloned {
		t.Fatalf("Expected RescanRequest to set CloneStatus=CLONED, got %+v", rescanned)
	}
	deadline = time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		curr, _ := svc.GetRequest("req-failed-old")
		if curr.State == StateAutoApproved && curr.Error == "" {
			break
		}
		time.Sleep(25 * time.Millisecond)
	}
	finalOld, _ := svc.GetRequest("req-failed-old")
	if finalOld.State != StateAutoApproved || finalOld.ReviewStatus != ReviewStatusReviewRequired || finalOld.Error != "" {
		t.Fatalf("Expected req-failed-old to recover cleanly, got %+v", finalOld)
	}
}
