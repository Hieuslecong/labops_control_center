package repositoryguard

import (
	"archive/tar"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
	"path/filepath"
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

	// B. Leaked Secret -> REJECTED
	secretReport := &ScanReport{
		Coverage: ScanCoverage{
			OverallCoverage: "POLICY_COMPLETE",
		},
		SecretsCount: 1,
	}
	verdictSec, _ := EvaluatePolicy(secretReport)
	if verdictSec != StateRejected {
		t.Fatalf("Secret report expected REJECTED, got %s", verdictSec)
	}

	// C. Critical Vulnerability -> REJECTED
	critReport := &ScanReport{
		Coverage: ScanCoverage{
			OverallCoverage: "POLICY_COMPLETE",
		},
		Vulnerabilities: VulnerabilitySummary{Critical: 1},
	}
	verdictCrit, _ := EvaluatePolicy(critReport)
	if verdictCrit != StateRejected {
		t.Fatalf("Critical report expected REJECTED, got %s", verdictCrit)
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
	hasher := sha256.New()
	hasher.Write(content)
	digest := hex.EncodeToString(hasher.Sum(nil))

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
