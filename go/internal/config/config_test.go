package config

import (
	"os"
	"testing"
)

func TestRepositoryGuardDataDirectoryRequiresExplicitConfiguration(t *testing.T) {
	original, hadOriginal := os.LookupEnv("LABOPS_REPO_GUARD_DATA_DIR")
	if err := os.Unsetenv("LABOPS_REPO_GUARD_DATA_DIR"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if hadOriginal {
			_ = os.Setenv("LABOPS_REPO_GUARD_DATA_DIR", original)
		} else {
			_ = os.Unsetenv("LABOPS_REPO_GUARD_DATA_DIR")
		}
	})

	if got := LoadFromEnv().RepoGuardDataDir; got != "" {
		t.Fatalf("Repository Guard data directory must not default to ephemeral storage, got %q", got)
	}
}

func TestRepositoryGuardDataDirectoryUsesExplicitValue(t *testing.T) {
	original, hadOriginal := os.LookupEnv("LABOPS_REPO_GUARD_DATA_DIR")
	if err := os.Setenv("LABOPS_REPO_GUARD_DATA_DIR", "/var/lib/labops/repositoryguard"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if hadOriginal {
			_ = os.Setenv("LABOPS_REPO_GUARD_DATA_DIR", original)
		} else {
			_ = os.Unsetenv("LABOPS_REPO_GUARD_DATA_DIR")
		}
	})

	if got := LoadFromEnv().RepoGuardDataDir; got != "/var/lib/labops/repositoryguard" {
		t.Fatalf("explicit Repository Guard data directory = %q", got)
	}
}
