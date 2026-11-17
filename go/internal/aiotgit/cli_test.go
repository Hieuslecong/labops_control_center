package aiotgit

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

type roundTripFunc func(*http.Request) *http.Response

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) { return f(req), nil }

func response(status int, body []byte, headers map[string]string) *http.Response {
	h := make(http.Header)
	for key, value := range headers {
		h.Set(key, value)
	}
	return &http.Response{StatusCode: status, Header: h, Body: io.NopCloser(bytes.NewReader(body))}
}

func makeWorkspaceToken(t *testing.T) string {
	t.Helper()
	claims := map[string]any{
		"iss": "labops-aiotgit", "aud": "labops-repositoryguard", "sub": "alice",
		"role": "RESEARCHER", "member_id": "member-alice", "workspace_id": "ws-0123456789",
		"node_id": "track", "scopes": []string{"repo:request:submit", "repo:request:list", "repo:request:read", "repo:bundle:read"},
		"resource_id": "", "jti": "0123456789abcdef0123456789abcdef", "iat": int64(1), "exp": int64(2),
	}
	data, err := json.Marshal(claims)
	if err != nil {
		t.Fatal(err)
	}
	return base64.RawURLEncoding.EncodeToString(data) + ".signature"
}

func runGit(t *testing.T, dir string, args ...string) string {
	t.Helper()
	command := exec.Command("git", append([]string{"-C", dir}, args...)...)
	command.Env = []string{"PATH=/usr/bin:/bin", "HOME=/nonexistent", "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_GLOBAL=/dev/null", "GIT_TERMINAL_PROMPT=0"}
	result, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, result)
	}
	return strings.TrimSpace(string(result))
}

func approvedBundle(t *testing.T) ([]byte, string, string) {
	t.Helper()
	root := t.TempDir()
	repo := filepath.Join(root, "source")
	if err := os.Mkdir(repo, 0700); err != nil {
		t.Fatal(err)
	}
	init := exec.Command("git", "init", "--template=/dev/null", repo)
	init.Env = []string{"PATH=/usr/bin:/bin", "HOME=/nonexistent", "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_GLOBAL=/dev/null"}
	if output, err := init.CombinedOutput(); err != nil {
		t.Fatalf("git init: %v\n%s", err, output)
	}
	_ = runGit(t, repo, "config", "user.name", "Test")
	_ = runGit(t, repo, "config", "user.email", "test@example.invalid")
	if err := os.WriteFile(filepath.Join(repo, "README.md"), []byte("approved\n"), 0600); err != nil {
		t.Fatal(err)
	}
	_ = runGit(t, repo, "add", "README.md")
	_ = runGit(t, repo, "commit", "-m", "approved")
	_ = runGit(t, repo, "branch", "aiotgit-approved")
	commit := runGit(t, repo, "rev-parse", "HEAD")
	bundlePath := filepath.Join(root, "approved.bundle")
	command := exec.Command("git", "-C", repo, "bundle", "create", bundlePath, "refs/heads/aiotgit-approved")
	command.Env = []string{"PATH=/usr/bin:/bin", "HOME=/nonexistent", "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_GLOBAL=/dev/null"}
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("git bundle create: %v\n%s", err, output)
	}
	data, err := os.ReadFile(bundlePath)
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(data)
	return data, fmt.Sprintf("%x", digest), commit
}

func TestCloneCreatesGitWorkingTreeFromApprovedBundle(t *testing.T) {
	bundle, digest, commit := approvedBundle(t)
	root := t.TempDir()
	requestID := "req-0123456789ab"
	token := makeWorkspaceToken(t)
	client, err := NewClient("http://127.0.0.1/api/v1/repositories", token, &http.Client{Transport: roundTripFunc(func(req *http.Request) *http.Response {
		if req.Header.Get("Authorization") != "Bearer "+token {
			t.Errorf("request did not carry the scoped token")
		}
		switch {
		case req.Method == http.MethodPost && req.URL.Path == "/api/v1/repositories/cli/requests":
			var payload struct {
				TargetWorkspace string `json:"target_workspace"`
				RepoURL         string `json:"repo_url"`
			}
			if err := json.NewDecoder(req.Body).Decode(&payload); err != nil {
				t.Errorf("decode create request: %v", err)
			}
			if payload.TargetWorkspace != "ws-0123456789" || payload.RepoURL != "https://github.com/example/project.git" {
				t.Errorf("unexpected submitted request: %+v", payload)
			}
			return response(http.StatusAccepted, []byte(`{"id":"`+requestID+`","state":"AUTO_APPROVED","commit_sha":"`+commit+`","git_bundle_digest":"`+digest+`"}`), nil)
		case req.Method == http.MethodGet && req.URL.Path == "/api/v1/repositories/cli/requests/"+requestID:
			return response(http.StatusOK, []byte(`{"id":"`+requestID+`","state":"AUTO_APPROVED","commit_sha":"`+commit+`","git_bundle_digest":"`+digest+`"}`), nil)
		case req.Method == http.MethodGet && req.URL.Path == "/api/v1/repositories/cli/requests/"+requestID+"/bundle":
			return response(http.StatusOK, bundle, map[string]string{"X-LabOps-Artifact-SHA256": digest, "X-LabOps-Commit-SHA": commit})
		default:
			return response(http.StatusNotFound, []byte(`{"detail":"not found"}`), nil)
		}
	})})
	if err != nil {
		t.Fatal(err)
	}
	code := Run([]string{"clone", "-b", "main", "https://github.com/example/project.git", "my-project"}, Config{
		Client: client, WorkspaceRoot: root,
	}, io.Discard, io.Discard)
	if code != 0 {
		t.Fatalf("aiotgit clone exit code = %d", code)
	}
	destination := filepath.Join(root, "my-project")
	if info, err := os.Stat(filepath.Join(destination, ".git")); err != nil || !info.IsDir() {
		t.Fatalf("valid Git metadata missing: %v", err)
	}
	if got := runGit(t, destination, "rev-parse", "HEAD"); got != commit {
		t.Fatalf("HEAD = %s, want approved commit %s", got, commit)
	}
	if got := runGit(t, destination, "symbolic-ref", "--short", "HEAD"); got != "main" {
		t.Fatalf("local branch = %s, want requested branch main", got)
	}
	if got := runGit(t, destination, "status", "--porcelain"); got != "" {
		t.Fatalf("fresh clone is dirty: %s", got)
	}
	if _, err := exec.Command("git", "-C", destination, "config", "--get", "remote.origin.url").Output(); err == nil {
		t.Fatal("clone retained a remote pointing to the temporary broker bundle")
	}
	if err := os.WriteFile(filepath.Join(destination, "local.txt"), []byte("local work\n"), 0600); err != nil {
		t.Fatal(err)
	}
	_ = runGit(t, destination, "add", "local.txt")
	_ = runGit(t, destination, "config", "user.name", "Alice")
	_ = runGit(t, destination, "config", "user.email", "alice@example.invalid")
	_ = runGit(t, destination, "commit", "-m", "local change")
}

func TestCloneRejectsTamperedBundleWithoutPublishing(t *testing.T) {
	bundle, digest, commit := approvedBundle(t)
	root := t.TempDir()
	requestID := "req-0123456789ab"
	token := makeWorkspaceToken(t)
	wrongDigest := strings.Repeat("0", 64)
	client, err := NewClient("http://127.0.0.1/api/v1/repositories", token, &http.Client{Transport: roundTripFunc(func(req *http.Request) *http.Response {
		switch req.URL.Path {
		case "/api/v1/repositories/cli/requests":
			return response(http.StatusAccepted, []byte(`{"id":"`+requestID+`","state":"AUTO_APPROVED","commit_sha":"`+commit+`","git_bundle_digest":"`+digest+`"}`), nil)
		case "/api/v1/repositories/cli/requests/" + requestID + "/bundle":
			return response(http.StatusOK, bundle, map[string]string{"X-LabOps-Artifact-SHA256": wrongDigest, "X-LabOps-Commit-SHA": commit})
		default:
			return response(http.StatusNotFound, nil, nil)
		}
	})})
	if err != nil {
		t.Fatal(err)
	}
	if code := Run([]string{"clone", "https://github.com/example/project", "project"}, Config{Client: client, WorkspaceRoot: root}, io.Discard, io.Discard); code == 0 {
		t.Fatal("tampered bundle was accepted")
	}
	if _, err := os.Lstat(filepath.Join(root, "project")); !os.IsNotExist(err) {
		t.Fatalf("tampered artifact was published: %v", err)
	}
}

func TestPendingCloneAndBrokerFailureDoNotCreateDestination(t *testing.T) {
	for _, status := range []int{http.StatusAccepted, http.StatusServiceUnavailable} {
		t.Run(fmt.Sprintf("status-%d", status), func(t *testing.T) {
			root := t.TempDir()
			calls := 0
			client, err := NewClient("http://127.0.0.1/api/v1/repositories", makeWorkspaceToken(t), &http.Client{Transport: roundTripFunc(func(req *http.Request) *http.Response {
				calls++
				if status == http.StatusServiceUnavailable {
					return response(status, []byte(`{"detail":"unavailable"}`), nil)
				}
				return response(http.StatusAccepted, []byte(`{"id":"req-abcdef123456","state":"REVIEW_REQUIRED"}`), nil)
			})})
			if err != nil {
				t.Fatal(err)
			}
			code := Run([]string{"clone", "https://github.com/example/project", "project"}, Config{Client: client, WorkspaceRoot: root}, io.Discard, io.Discard)
			if code == 0 {
				t.Fatal("pending/unavailable broker was reported as a successful clone")
			}
			if calls != 1 {
				t.Fatalf("unexpected broker requests before pending result: %d", calls)
			}
			if _, err := os.Lstat(filepath.Join(root, "project")); !os.IsNotExist(err) {
				t.Fatalf("unapproved source created in workspace: %v", err)
			}
		})
	}
}

func TestCloneRejectsDestinationEscapeBeforeBrokerCall(t *testing.T) {
	root := t.TempDir()
	calls := 0
	client, err := NewClient("http://127.0.0.1/api/v1/repositories", makeWorkspaceToken(t), &http.Client{Transport: roundTripFunc(func(req *http.Request) *http.Response {
		calls++
		return response(http.StatusInternalServerError, nil, nil)
	})})
	if err != nil {
		t.Fatal(err)
	}
	if code := Run([]string{"clone", "https://github.com/example/project", "../escape"}, Config{Client: client, WorkspaceRoot: root}, io.Discard, io.Discard); code == 0 {
		t.Fatal("path escape was accepted")
	}
	if calls != 0 {
		t.Fatal("broker contacted before destination validation")
	}
}

func TestCloneRejectsSymlinkParentBeforeBrokerCall(t *testing.T) {
	root, outside := t.TempDir(), t.TempDir()
	if err := os.Symlink(outside, filepath.Join(root, "link")); err != nil {
		t.Fatal(err)
	}
	calls := 0
	client, err := NewClient("http://127.0.0.1/api/v1/repositories", makeWorkspaceToken(t), &http.Client{Transport: roundTripFunc(func(req *http.Request) *http.Response {
		calls++
		return response(http.StatusInternalServerError, nil, nil)
	})})
	if err != nil {
		t.Fatal(err)
	}
	if code := Run([]string{"clone", "https://github.com/example/project", "link/project"}, Config{Client: client, WorkspaceRoot: root}, io.Discard, io.Discard); code == 0 {
		t.Fatal("symlink destination parent was accepted")
	}
	if calls != 0 {
		t.Fatal("Broker was contacted before symlink destination validation")
	}
	if _, err := os.Lstat(filepath.Join(outside, "project")); !os.IsNotExist(err) {
		t.Fatalf("symlink escape created files: %v", err)
	}
}

func TestStatusUsesWorkspaceScopedBrokerResponse(t *testing.T) {
	requestID := "req-abcdef123456"
	calls := 0
	client, err := NewClient("http://127.0.0.1/api/v1/repositories", makeWorkspaceToken(t), &http.Client{Transport: roundTripFunc(func(req *http.Request) *http.Response {
		calls++
		if req.URL.Path != "/api/v1/repositories/cli/requests" {
			t.Errorf("unexpected status path: %s", req.URL.Path)
		}
		return response(http.StatusOK, []byte(`[{"id":"`+requestID+`","repo_url":"https://github.com/example/project","requested_ref":"main","state":"REVIEW_REQUIRED"}]`), nil)
	})})
	if err != nil {
		t.Fatal(err)
	}
	var stdout, stderr bytes.Buffer
	if code := Run([]string{"status"}, Config{Client: client}, &stdout, &stderr); code != 0 {
		t.Fatalf("status exit code=%d, error=%s", code, stderr.String())
	}
	if calls != 1 || !strings.Contains(stdout.String(), requestID) || !strings.Contains(stdout.String(), "REVIEW_REQUIRED") {
		t.Fatalf("status output/calls unexpected: calls=%d output=%q", calls, stdout.String())
	}
}

func TestExplicitStatusQueriesOnlyRequestedID(t *testing.T) {
	requestID := "req-abcdef123456"
	calls := 0
	client, err := NewClient("http://127.0.0.1/api/v1/repositories", makeWorkspaceToken(t), &http.Client{Transport: roundTripFunc(func(req *http.Request) *http.Response {
		calls++
		if req.URL.Path != "/api/v1/repositories/cli/requests/"+requestID {
			t.Errorf("unexpected explicit status path: %s", req.URL.Path)
		}
		return response(http.StatusOK, []byte(`{"id":"`+requestID+`","state":"REJECTED","error":"policy denied"}`), nil)
	})})
	if err != nil {
		t.Fatal(err)
	}
	var stdout, stderr bytes.Buffer
	if code := Run([]string{"status", requestID}, Config{Client: client}, &stdout, &stderr); code != 0 {
		t.Fatalf("status exit code=%d, error=%s", code, stderr.String())
	}
	if calls != 1 || !strings.Contains(stdout.String(), "policy denied") {
		t.Fatalf("status response missing reason: calls=%d output=%q", calls, stdout.String())
	}
}
