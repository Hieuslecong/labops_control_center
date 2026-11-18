package gitaiotcli

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestResolveToken_FailClosedAndPermCheck(t *testing.T) {
	t.Setenv("LABOPS_TOKEN", "")
	t.Setenv("LABOPS_TOKEN_FILE", filepath.Join(t.TempDir(), "missing-token"))

	if _, err := ResolveToken(&Options{}); err == nil {
		t.Fatal("ResolveToken MUST fail closed when no env var or token file exists")
	}

	tempDir := t.TempDir()
	insecureFile := filepath.Join(tempDir, "token-insecure")
	if err := os.WriteFile(insecureFile, []byte("secret-token\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := ResolveToken(&Options{TokenFile: insecureFile}); err == nil {
		t.Fatal("ResolveToken MUST reject 0644 token file")
	}

	secureFile := filepath.Join(tempDir, "token-secure")
	if err := os.WriteFile(secureFile, []byte("tok-verified-123\n"), 0600); err != nil {
		t.Fatal(err)
	}
	tok, err := ResolveToken(&Options{TokenFile: secureFile})
	if err != nil {
		t.Fatalf("ResolveToken failed for 0600 file: %v", err)
	}
	if tok != "tok-verified-123" {
		t.Fatalf("Expected trimmed token, got %q", tok)
	}
}

func TestCLI_CloneStatusDeliver_Contract(t *testing.T) {
	var pollCount atomic.Int32
	var capturedSubmitBody map[string]interface{}
	var capturedDeliverBody map[string]interface{}
	var capturedAuth string

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		capturedAuth = r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")

		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/v1/repositories/requests":
			raw, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(raw, &capturedSubmitBody)
			w.WriteHeader(http.StatusAccepted)
			_ = json.NewEncoder(w).Encode(map[string]interface{}{
				"id":    "req-cli-101",
				"state": "REQUESTED",
			})

		case r.Method == http.MethodGet && r.URL.Path == "/api/v1/repositories/requests/req-cli-101":
			n := pollCount.Add(1)
			state := "SCANNING"
			if n >= 2 {
				state = "AUTO_APPROVED"
			}
			_ = json.NewEncoder(w).Encode(map[string]interface{}{
				"id":              "req-cli-101",
				"state":           state,
				"commit_sha":      "1234567890abcdef1234567890abcdef12345678",
				"artifact_digest": "deadbeef",
			})

		case r.Method == http.MethodPost && r.URL.Path == "/api/v1/repositories/deliver":
			raw, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(raw, &capturedDeliverBody)
			_ = json.NewEncoder(w).Encode(map[string]interface{}{
				"success": true,
			})

		case r.Method == http.MethodGet && r.URL.Path == "/api/v1/repositories/requests":
			_ = json.NewEncoder(w).Encode([]map[string]interface{}{
				{
					"id":           "req-cli-101",
					"repo_url":     "https://github.com/org/demo",
					"commit_sha":   "1234567890abcdef1234567890abcdef12345678",
					"submitted_by": "alice",
					"state":        "DELIVERED",
				},
			})

		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	var stdout, stderr bytes.Buffer
	opts := Options{
		APIBaseURL:   srv.URL + "/api/v1/repositories",
		Token:        "bearer-test-token",
		PollInterval: 10 * time.Millisecond,
		MaxWait:      5 * time.Second,
		Stdout:       &stdout,
		Stderr:       &stderr,
	}

	code := RunWithOptions([]string{"clone", "-b", "main", "https://github.com/org/demo", "./rel-out"}, "gitaiot", opts)
	if code != 0 {
		t.Fatalf("clone expected exit 0, got %d (stderr=%s)", code, stderr.String())
	}

	if capturedAuth != "Bearer bearer-test-token" {
		t.Fatalf("Expected Bearer auth header, got %q", capturedAuth)
	}
	if _, hasSubmitter := capturedSubmitBody["submitter"]; hasSubmitter {
		t.Fatalf("CLI MUST NOT send spoofable submitter in request payload: %+v", capturedSubmitBody)
	}
	if capturedSubmitBody["ref"] != "main" {
		t.Fatalf("Expected ref=main, got %+v", capturedSubmitBody["ref"])
	}
	destVal, _ := capturedDeliverBody["target_directory"].(string)
	if !filepath.IsAbs(destVal) || !strings.HasSuffix(destVal, "/rel-out") {
		t.Fatalf("Expected absolute target_directory ending in /rel-out, got %q", destVal)
	}

	// Check status listing
	stdout.Reset()
	stderr.Reset()
	code = RunWithOptions([]string{"status"}, "gitaiot", opts)
	if code != 0 {
		t.Fatalf("status expected exit 0, got %d (%s)", code, stderr.String())
	}
	if !strings.Contains(stdout.String(), "req-cli-101") {
		t.Fatalf("Expected req-cli-101 in status table output: %s", stdout.String())
	}
}
