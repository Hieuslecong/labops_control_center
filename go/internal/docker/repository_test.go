package docker

import (
	"os"
	"path/filepath"
	"testing"
)

func TestPrepareRepositoryCredentialsCanRetry(t *testing.T) {
	runtime := t.TempDir()
	req := ProvisionRequest{
		RepositoryAPIURL: "https://broker.example/api/v1/repositories",
		RepositoryToken:  "first-fixture",
		UID:              os.Geteuid(),
		GID:              os.Getegid(),
	}
	controller := &Controller{}
	dir, err := controller.prepareRepositoryCredentials(req, runtime)
	if err != nil {
		t.Fatal(err)
	}
	req.RepositoryToken = "replacement-fixture"
	if _, err := controller.prepareRepositoryCredentials(req, runtime); err != nil {
		t.Fatal(err)
	}
	token := filepath.Join(dir, "repository-token")
	info, err := os.Stat(token)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0600 {
		t.Fatalf("credential mode = %o", info.Mode().Perm())
	}
	content, err := os.ReadFile(token)
	if err != nil || string(content) != "replacement-fixture\n" {
		t.Fatal("retry did not atomically replace the credential")
	}
}

func TestRuntimeValidationBeforeWritingFiles(t *testing.T) {
	for _, input := range []struct{ id, user, mount string }{
		{"ws-fixture", "root", "/workspace"},
		{"ws-fixture", "user\nAllowUsers anyone", "/workspace"},
		{"ws-fixture", "alice", "/workspace:ro"},
		{"../fixture", "alice", "/workspace"},
	} {
		root := t.TempDir()
		controller := &Controller{runtimeRoot: root}
		if _, err := controller.prepareWorkspaceRuntimeDir(input.id, input.user, 20000, 20000, input.mount); err == nil {
			t.Fatal("invalid workspace configuration accepted")
		}
		if entries, err := os.ReadDir(root); err != nil || len(entries) != 0 {
			t.Fatalf("validation wrote runtime files: %v %v", entries, err)
		}
	}
}
