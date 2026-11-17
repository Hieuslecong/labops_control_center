package pki

import (
	"crypto/x509"
	"crypto/x509/pkix"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func TestEmptyAllowlistRejectsUnknownNode(t *testing.T) {
	if NewRevocationManager().IsNodeAllowed("unknown") {
		t.Fatal("empty allowlist must reject unknown nodes")
	}
}

func TestBootstrapRequiresExplicitAllowlistAndNeverOverwritesExistingState(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	rm := NewRevocationManagerWithPersistence(path)
	if err := rm.BootstrapAllowedNodes(nil); err == nil {
		t.Fatal("empty bootstrap must fail")
	}
	if err := rm.BootstrapAllowedNodes([]string{"track", "workstation"}); err != nil {
		t.Fatal(err)
	}
	if !rm.IsNodeAllowed("track") || !rm.IsNodeAllowed("workstation") {
		t.Fatal("explicit bootstrap nodes were not allowed")
	}
	reloaded := NewRevocationManagerWithPersistence(path)
	if err := reloaded.BootstrapAllowedNodes([]string{"attacker"}); err == nil {
		t.Fatal("bootstrap overwrote existing persisted state")
	}
	if reloaded.IsNodeAllowed("attacker") {
		t.Fatal("restart implicitly allowed a new node")
	}
}

func TestPersistenceFailureFailsClosed(t *testing.T) {
	path := filepath.Join(t.TempDir(), "missing", "state.json")
	rm := NewRevocationManagerWithPersistence(path)
	if err := rm.AllowNode("node"); err == nil {
		t.Fatal("write failure was ignored")
	}
	if rm.IsNodeAllowed("node") {
		t.Fatal("node allowed despite persistence failure")
	}
}

func TestCorruptStateFailsClosed(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	if err := os.WriteFile(path, []byte("not json"), 0600); err != nil {
		t.Fatal(err)
	}
	rm := NewRevocationManagerWithPersistence(path)
	if err := rm.LoadError(); err == nil {
		t.Fatal("corruption ignored")
	}
	if err := rm.AllowNode("node"); err == nil {
		t.Fatal("corrupt state overwritten")
	}
}

func TestRevokedNodeCannotBeReallowedOnEnrollment(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	rm := NewRevocationManagerWithPersistence(path)
	if err := rm.AllowNode("node"); err != nil {
		t.Fatal(err)
	}
	if err := rm.RevokeNode("node", "compromised"); err != nil {
		t.Fatal(err)
	}
	rm = NewRevocationManagerWithPersistence(path)
	if err := rm.AllowNode("node"); err == nil {
		t.Fatal("revoked node was allowed again")
	}
	if rm.IsNodeAllowed("node") {
		t.Fatal("revoked node permitted")
	}
}

func TestConcurrentRevocationsSurviveReload(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	rm := NewRevocationManagerWithPersistence(path)
	var wg sync.WaitGroup
	errs := make(chan error, 12)
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func(i int) { defer wg.Done(); errs <- rm.RevokeNode(fmt.Sprintf("node-%d", i), "test") }(i)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	reloaded := NewRevocationManagerWithPersistence(path)
	if err := reloaded.LoadError(); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 12; i++ {
		id := fmt.Sprintf("node-%d", i)
		if err := reloaded.AllowNode(id); err == nil {
			t.Fatalf("lost revocation for %s", id)
		}
	}
}

func TestPersistenceFaultsFailClosedInCurrentProcess(t *testing.T) {
	for _, stage := range []string{"create", "write", "file_sync", "rename", "dir_sync"} {
		t.Run(stage, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "state.json")
			rm := NewRevocationManagerWithPersistence(path)
			rm.persistHook = func(at string) error {
				if at == stage {
					return os.ErrPermission
				}
				return nil
			}
			if err := rm.AllowNode("node"); err == nil {
				t.Fatal("expected persistence error")
			}
			if rm.IsNodeAllowed("node") {
				t.Fatal("persistence failure must deny every node in the running process")
			}
			if err := rm.LoadError(); err == nil {
				t.Fatal("persistence failure not latched")
			}
		})
	}
}

func TestRevocationFaultsFailClosedInCurrentProcess(t *testing.T) {
	for _, stage := range []string{"create", "write", "file_sync", "rename", "dir_sync"} {
		t.Run(stage, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "state.json")
			rm := NewRevocationManagerWithPersistence(path)
			if err := rm.AllowNode("node"); err != nil {
				t.Fatal(err)
			}
			rm.persistHook = func(at string) error {
				if at == stage {
					return os.ErrPermission
				}
				return nil
			}
			if err := rm.RevokeNode("node", "test"); err == nil {
				t.Fatal("expected persistence error")
			}
			if rm.IsNodeAllowed("node") {
				t.Fatal("failed revocation must fail closed in the running process")
			}
		})
	}
}

func TestFailedRevocationLeavesRestartFailClosedMarker(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	rm := NewRevocationManagerWithPersistence(path)
	if err := rm.AllowNode("track"); err != nil {
		t.Fatal(err)
	}
	rm.persistHook = func(stage string) error {
		if stage == "write" {
			return os.ErrPermission
		}
		return nil
	}
	if err := rm.RevokeNode("track", "test"); err == nil {
		t.Fatal("expected persistence failure")
	}
	restarted := NewRevocationManagerWithPersistence(path)
	if restarted.LoadError() == nil {
		t.Fatal("restart did not notice incomplete revocation update")
	}
	if restarted.IsNodeAllowed("track") {
		t.Fatal("restart allowed node after incomplete revocation update")
	}
}

func TestCertificateIdentityRequiresNodeSAN(t *testing.T) {
	if _, err := ExtractNodeIDFromCert(&x509.Certificate{Subject: pkix.Name{CommonName: "track"}}); err == nil {
		t.Fatal("certificate common name alone must not establish node identity")
	}
}

func TestEnrollmentNeedsConfiguredStrongSecretAndValidNode(t *testing.T) {
	if token := NewEnrollmentTokenManager("").GenerateToken("track", time.Minute); token != "" {
		t.Fatal("missing token secret must not issue an enrollment token")
	}
	manager := NewEnrollmentTokenManager("0123456789abcdef0123456789abcdef")
	if manager.GenerateToken("../track", time.Minute) != "" {
		t.Fatal("invalid node ID accepted")
	}
	if manager.GenerateToken("track", 2*time.Hour) != "" {
		t.Fatal("excessive token lifetime accepted")
	}
}
