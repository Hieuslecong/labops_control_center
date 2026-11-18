package config

import (
	"os"
	"testing"
)

func TestLoadFromEnv_NoHardcodedSecretFallback(t *testing.T) {
	t.Setenv("LABOPS_AGENT_SECRET", "")
	cfg := LoadFromEnv()
	if cfg.SecretToken != "" {
		t.Fatalf("expected empty SecretToken when LABOPS_AGENT_SECRET unset, got len=%d", len(cfg.SecretToken))
	}
	if err := cfg.Validate(); err == nil {
		t.Fatalf("expected Validate() to fail closed when LABOPS_AGENT_SECRET is empty")
	}

	_ = os.Setenv("LABOPS_AGENT_SECRET", "short")
	cfgShort := LoadFromEnv()
	if err := cfgShort.Validate(); err == nil {
		t.Fatalf("expected Validate() to reject short secret < 16 chars")
	}

	t.Setenv("LABOPS_AGENT_SECRET", "super-strong-dedicated-agent-secret-2026")
	cfgValid := LoadFromEnv()
	if err := cfgValid.Validate(); err != nil {
		t.Fatalf("expected Validate() to pass with valid secret, got: %v", err)
	}
}
