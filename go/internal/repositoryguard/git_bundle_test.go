package repositoryguard

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func runTestGit(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-C", dir}, args...)...)
	cmd.Env = []string{"PATH=/usr/bin:/bin", "HOME=/nonexistent", "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_GLOBAL=/dev/null", "GIT_TERMINAL_PROMPT=0"}
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v failed: %v\n%s", args, err, output)
	}
	return strings.TrimSpace(string(output))
}

func TestCreateGitBundlePreservesApprovedCommitAndGitMetadata(t *testing.T) {
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
	_ = runTestGit(t, repo, "config", "user.name", "LabOps Test")
	_ = runTestGit(t, repo, "config", "user.email", "labops-test@example.invalid")
	if err := os.WriteFile(filepath.Join(repo, "README.md"), []byte("approved source\n"), 0600); err != nil {
		t.Fatal(err)
	}
	_ = runTestGit(t, repo, "add", "README.md")
	_ = runTestGit(t, repo, "commit", "-m", "approved snapshot")
	commit := runTestGit(t, repo, "rev-parse", "HEAD")
	bundle := filepath.Join(root, "approved.bundle")

	if err := createGitBundle(context.Background(), repo, bundle, commit); err != nil {
		t.Fatalf("createGitBundle() error = %v", err)
	}
	clone := filepath.Join(root, "workspace-project")
	cmd := exec.Command("git", "-c", "core.hooksPath=/dev/null", "clone", "--branch", "aiotgit-approved", bundle, clone)
	cmd.Env = []string{"PATH=/usr/bin:/bin", "HOME=/nonexistent", "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_GLOBAL=/dev/null", "GIT_TERMINAL_PROMPT=0"}
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("clone verified bundle: %v\n%s", err, output)
	}
	if got := runTestGit(t, clone, "rev-parse", "HEAD"); got != commit {
		t.Fatalf("cloned HEAD = %s, want approved commit %s", got, commit)
	}
	if got := runTestGit(t, clone, "status", "--porcelain"); got != "" {
		t.Fatalf("fresh clone is not clean: %q", got)
	}
}
