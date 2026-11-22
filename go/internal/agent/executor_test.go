package agent

import (
	"context"
	"testing"

	"github.com/Hieuslecong/labops_control_center/go/internal/config"
)

func TestManagedProvisionIdempotencyIgnoresFreshCredential(t *testing.T) {
	const action = "PROVISION_DOCKER_WORKSPACE"
	first := map[string]string{"spec_json": `{"repository_token":"one","correlation_id":"first","repository_api_url":"https://broker","uid":20000}`}
	retry := map[string]string{"spec_json": `{"repository_token":"two","correlation_id":"retry","repository_api_url":"https://broker","uid":20000}`}
	changed := map[string]string{"spec_json": `{"repository_token":"two","correlation_id":"retry","repository_api_url":"https://broker","uid":20001}`}
	if idempotencyParamsHash(action, first) != idempotencyParamsHash(action, retry) {
		t.Fatal("fresh credential must not conflict with an equivalent provision retry")
	}
	if idempotencyParamsHash(action, first) == idempotencyParamsHash(action, changed) {
		t.Fatal("workspace configuration change must conflict with prior provisioning")
	}
	if first["spec_json"] != `{"repository_token":"one","correlation_id":"first","repository_api_url":"https://broker","uid":20000}` {
		t.Fatal("must preserve the signed command parameters")
	}
}

func TestMutationRequiresDelegationWithNoSecret(t *testing.T) {
	executor := &CommandExecutor{cfg: &config.AgentConfig{ServerID: "workstation"}}
	for _, action := range []string{"TERMINATE_PROCESS", "PROVISION_DOCKER_WORKSPACE", "STOP_DOCKER_WORKSPACE", "SYNC_SSH_AUTHORIZED_KEYS", "PROVISION_MEMBER_STORAGE"} {
		result := executor.Execute(context.Background(), &ExecuteCommandRequest{Action: action, ActorID: "admin"})
		if result.Success || result.ErrorCode != "DELEGATION_INVALID" {
			t.Fatalf("%s: expected rejection before execution, got %+v", action, result)
		}
	}
}
