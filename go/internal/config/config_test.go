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

func TestLoadFromEnv_NodeTokenAndBrokerSecretFallback(t *testing.T) {
	// Explicit empty LABOPS_AGENT_SECRET must still fail closed even when LABOPS_NODE_AGENT_TOKEN is set.
	t.Setenv("LABOPS_AGENT_SECRET", "")
	t.Setenv("LABOPS_NODE_AGENT_TOKEN", "super-strong-node-agent-token-2026")
	cfgExplicitEmpty := LoadFromEnv()
	if cfgExplicitEmpty.SecretToken != "" {
		t.Fatalf("expected empty SecretToken when LABOPS_AGENT_SECRET explicitly empty, got len=%d", len(cfgExplicitEmpty.SecretToken))
	}
	if err := cfgExplicitEmpty.Validate(); err == nil {
		t.Fatalf("expected Validate() to fail closed when LABOPS_AGENT_SECRET is explicitly empty")
	}

	// Unset LABOPS_AGENT_SECRET falls back cleanly to LABOPS_NODE_AGENT_TOKEN.
	if err := os.Unsetenv("LABOPS_AGENT_SECRET"); err != nil {
		t.Fatalf("failed to unset LABOPS_AGENT_SECRET: %v", err)
	}
	cfgFallback := LoadFromEnv()
	if cfgFallback.SecretToken != "super-strong-node-agent-token-2026" {
		t.Fatalf("expected SecretToken fallback to LABOPS_NODE_AGENT_TOKEN")
	}
	if err := cfgFallback.Validate(); err != nil {
		t.Fatalf("expected Validate() to pass with LABOPS_NODE_AGENT_TOKEN fallback, got: %v", err)
	}

	// Broker secret accepts both LABOPS_BROKER_VERIFY_SECRET and legacy LABOPS_REPO_BROKER_SECRET.
	t.Setenv("LABOPS_BROKER_VERIFY_SECRET", "")
	t.Setenv("LABOPS_REPO_BROKER_SECRET", "legacy-broker-secret")
	if got := LoadFromEnv().BrokerVerifySecret; got != "legacy-broker-secret" {
		t.Fatalf("expected fallback to LABOPS_REPO_BROKER_SECRET, got %q", got)
	}

	t.Setenv("LABOPS_BROKER_VERIFY_SECRET", "primary-broker-secret")
	if got := LoadFromEnv().BrokerVerifySecret; got != "primary-broker-secret" {
		t.Fatalf("expected precedence for LABOPS_BROKER_VERIFY_SECRET, got %q", got)
	}
}
