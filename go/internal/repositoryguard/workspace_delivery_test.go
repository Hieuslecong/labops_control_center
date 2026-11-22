//go:build linux

package repositoryguard

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestManagedDeliveryDestinationGuards(t *testing.T) {
	stage, root := t.TempDir(), t.TempDir()
	t.Setenv("LABOPS_REPOSITORY_DELIVERY_STAGING", stage)
	if err := os.Chmod(stage, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(root, "existing"), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(t.TempDir(), filepath.Join(root, "link")); err != nil {
		t.Fatal(err)
	}
	for _, destination := range []string{"/etc/x", "../outside", "./one/../../outside", "existing", "link", "link/repo", ".git/repo"} {
		t.Run(destination, func(t *testing.T) {
			err := deliverToWorkspace(context.Background(), "", "", "", "", "", destination, WorkspaceDelivery{Root: root, UID: 20000, GID: 20000})
			if err == nil || err.Error() == "artifactPath, expectedDigest, and destDir must not be empty" {
				t.Fatalf("destination should be refused before artifact processing: %v", err)
			}
		})
	}
	entries, err := os.ReadDir(stage)
	if err != nil || len(entries) != 0 {
		t.Fatalf("refused destinations left staging files: %v, %v", entries, err)
	}
}

func TestManagedRequestCannotUseLegacyOrOtherWorkspaceDelivery(t *testing.T) {
	service, err := NewGuardService(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	service.requests["req"] = &RepositoryRequest{ID: "req", Submitter: "alice", TargetWorkspace: "managed:ws-alice", State: StateAutoApproved}
	if err := service.DeliverAuthorized("req", "alice", false, "/legacy"); !errors.Is(err, ErrPermissionDenied) {
		t.Fatalf("legacy delivery result: %v", err)
	}
	workspace := WorkspaceDelivery{Root: t.TempDir(), UID: 20000, GID: 20000}
	for _, identity := range []struct{ actor, workspaceID string }{{"alice", "ws-other"}, {"bob", "ws-alice"}} {
		if err := service.deliverAuthorized("req", identity.actor, false, "repo", identity.workspaceID, &workspace); !errors.Is(err, ErrPermissionDenied) {
			t.Fatalf("cross-workspace/member delivery result: %v", err)
		}
	}
}

func TestGuardRejectsNewScanWhenCapacityIsFull(t *testing.T) {
	service, err := NewGuardService(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	for range maxConcurrentScans {
		service.scanSlots <- struct{}{}
	}
	if _, err := service.SubmitRequest(context.Background(), "https://github.com/example/demo", "HEAD", "alice", "managed:ws-alice"); !errors.Is(err, ErrScanCapacity) {
		t.Fatalf("expected bounded scan admission, got %v", err)
	}
	if len(service.requests) != 0 {
		t.Fatal("overloaded submission left a request on disk")
	}
}
