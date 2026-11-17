package docker

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
)

type testTransport func(*http.Request) (*http.Response, error)

func (f testTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

type fakeRegistry struct {
	record WorkspaceRecord
	found  bool
	err    error
}

type reportingRegistry struct {
	fakeRegistry
	observations []RuntimeObservation
	workspaceID  string
}

func (r *reportingRegistry) RecordRuntimeObservation(_ context.Context, workspaceID string, observation RuntimeObservation) (WorkspaceRecord, error) {
	r.workspaceID = workspaceID
	r.observations = append(r.observations, observation)
	return WorkspaceRecord{
		WorkspaceID: workspaceID, ContainerID: observation.ContainerID, ContainerName: observation.ContainerName,
		OwnerID: observation.Labels["labops.owner_id"], NodeID: observation.Labels["labops.node_id"], State: "ACTIVE",
	}, nil
}

type changingRegistry struct{ calls int }

func (r *changingRegistry) LookupContainer(_ context.Context, _ string) (WorkspaceRecord, bool, error) {
	r.calls++
	record := testRecord()
	if r.calls > 1 {
		record.OwnerID = "bob"
	}
	return record, true, nil
}

func (r fakeRegistry) LookupContainer(_ context.Context, _ string) (WorkspaceRecord, bool, error) {
	return r.record, r.found, r.err
}

const registeredID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

func testRecord() WorkspaceRecord {
	return WorkspaceRecord{WorkspaceID: "workspace-1", ContainerID: registeredID, ContainerName: "labops-ws-alice",
		OwnerID: "alice", NodeID: "track", State: "ACTIVE"}
}

func inspectJSON(id, name, state string, labels map[string]string) string {
	return `{"Id":"` + id + `","Name":"/` + name + `","Created":"2026-01-01T00:00:00Z","State":{"Status":"` + state + `","Running":` + boolJSON(state == "running") + `},"HostConfig":{"Privileged":false,"NetworkMode":"bridge","Memory":1073741824,"NanoCpus":2000000000,"PidsLimit":512},"Mounts":[],"Config":{"Labels":{"labops.managed":"` + labels["labops.managed"] + `","labops.workspace_id":"` + labels["labops.workspace_id"] + `","labops.owner_id":"` + labels["labops.owner_id"] + `","labops.node_id":"` + labels["labops.node_id"] + `"}}}`
}
func boolJSON(v bool) string {
	if v {
		return "true"
	}
	return "false"
}
func recordLabels(r WorkspaceRecord) map[string]string {
	return map[string]string{"labops.managed": "true", "labops.workspace_id": r.WorkspaceID, "labops.owner_id": r.OwnerID, "labops.node_id": r.NodeID}
}
func jsonResponse(status int, body string) *http.Response {
	return &http.Response{StatusCode: status, Body: io.NopCloser(strings.NewReader(body)), Header: make(http.Header)}
}
func controllerWith(record WorkspaceRecord, found bool, roundTrip testTransport) *Controller {
	return &Controller{localNodeID: "track", registry: fakeRegistry{record: record, found: found}, client: &http.Client{Transport: roundTrip}}
}

func TestInspectRejectsSystemContainerBeforeDockerCall(t *testing.T) {
	calls := 0
	c := controllerWith(testRecord(), true, func(*http.Request) (*http.Response, error) { calls++; return jsonResponse(404, ""), nil })
	if _, err := c.InspectWorkspace(context.Background(), "postgres"); err == nil {
		t.Fatal("system container accepted")
	}
	if calls != 0 {
		t.Fatalf("Docker called for protected system container: %d", calls)
	}
}

func TestRegisteredWorkspaceStopVerified(t *testing.T) {
	r := testRecord()
	labels := recordLabels(r)
	calls := 0
	c := controllerWith(r, true, func(req *http.Request) (*http.Response, error) {
		calls++
		if req.Method == http.MethodPost {
			return jsonResponse(http.StatusNoContent, ""), nil
		}
		state := "running"
		if calls == 3 {
			state = "exited"
		}
		return jsonResponse(http.StatusOK, inspectJSON(registeredID, r.ContainerName, state, labels)), nil
	})
	err := c.StopWorkspace(context.Background(), WorkspaceActor{ID: "alice", Role: "RESEARCHER"}, r.WorkspaceID, registeredID, 10)
	if err != nil {
		t.Fatalf("legitimate registered workspace was denied: %v", err)
	}
	if calls != 3 {
		t.Fatalf("expected inspect-stop-postinspect; got %d calls", calls)
	}
}

func TestRegistryOwnershipChangeBeforeStopPreventsDockerMutation(t *testing.T) {
	registry := &changingRegistry{}
	dockerCalls, stopCalls := 0, 0
	c := &Controller{localNodeID: "track", registry: registry, client: &http.Client{Transport: testTransport(func(req *http.Request) (*http.Response, error) {
		dockerCalls++
		if req.Method == http.MethodPost {
			stopCalls++
			return jsonResponse(http.StatusNoContent, ""), nil
		}
		return jsonResponse(http.StatusOK, inspectJSON(registeredID, "labops-ws-alice", "running", recordLabels(testRecord()))), nil
	})}}
	err := c.StopWorkspace(context.Background(), WorkspaceActor{ID: "alice", Role: "RESEARCHER"}, "workspace-1", registeredID, 10)
	if err == nil {
		t.Fatal("stale workspace ownership was accepted")
	}
	if stopCalls != 0 {
		t.Fatalf("Docker stop occurred after registry ownership changed: %d stop calls", stopCalls)
	}
	if dockerCalls != 1 {
		t.Fatalf("expected inspect only before authorization conflict, got %d Docker calls", dockerCalls)
	}
}

func TestUntrustedOrMismatchedWorkspaceCannotStop(t *testing.T) {
	tests := []struct {
		name   string
		record WorkspaceRecord
		found  bool
		labels map[string]string
		actor  WorkspaceActor
		id     string
	}{
		{name: "fake ws prefix", record: WorkspaceRecord{}, found: false, labels: map[string]string{"labops.managed": "true"}, actor: WorkspaceActor{ID: "alice", Role: "RESEARCHER"}, id: registeredID},
		{name: "wrong owner", record: func() WorkspaceRecord { r := testRecord(); r.OwnerID = "bob"; return r }(), found: true, labels: func() map[string]string { r := testRecord(); r.OwnerID = "bob"; return recordLabels(r) }(), actor: WorkspaceActor{ID: "alice", Role: "RESEARCHER"}, id: registeredID},
		{name: "wrong node", record: func() WorkspaceRecord { r := testRecord(); r.NodeID = "workstation"; return r }(), found: true, labels: func() map[string]string { r := testRecord(); r.NodeID = "workstation"; return recordLabels(r) }(), actor: WorkspaceActor{ID: "alice", Role: "RESEARCHER"}, id: registeredID},
		{name: "label spoof", record: testRecord(), found: true, labels: map[string]string{"labops.managed": "true", "labops.workspace_id": "spoof", "labops.owner_id": "alice", "labops.node_id": "track"}, actor: WorkspaceActor{ID: "alice", Role: "RESEARCHER"}, id: registeredID},
		{name: "registry ID mismatch", record: func() WorkspaceRecord {
			r := testRecord()
			r.ContainerID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
			return r
		}(), found: true, labels: recordLabels(testRecord()), actor: WorkspaceActor{ID: "alice", Role: "RESEARCHER"}, id: registeredID},
		{name: "cross-user actor", record: testRecord(), found: true, labels: recordLabels(testRecord()), actor: WorkspaceActor{ID: "mallory", Role: "RESEARCHER"}, id: registeredID},
		{name: "invalid actor role", record: testRecord(), found: true, labels: recordLabels(testRecord()), actor: WorkspaceActor{ID: "alice", Role: "SERVICE"}, id: registeredID},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			calls := 0
			c := controllerWith(tt.record, tt.found, func(*http.Request) (*http.Response, error) {
				calls++
				return jsonResponse(200, inspectJSON(registeredID, "labops-ws-alice", "running", tt.labels)), nil
			})
			if err := c.StopWorkspace(context.Background(), tt.actor, "workspace-1", tt.id, 10); err == nil {
				t.Fatal("unauthorized workspace stop accepted")
			}
			expectedCalls := 1
			if tt.name == "invalid actor role" {
				expectedCalls = 0
			}
			if calls != expectedCalls {
				t.Fatalf("expected %d calls and no stop, got %d calls", expectedCalls, calls)
			}
		})
	}
}

func TestContainerReplacementAndSystemNamesDenied(t *testing.T) {
	r := testRecord()
	calls := 0
	c := controllerWith(r, true, func(*http.Request) (*http.Response, error) {
		calls++
		return jsonResponse(200, inspectJSON(strings.Repeat("b", 64), r.ContainerName, "running", recordLabels(r))), nil
	})
	if err := c.StopWorkspace(context.Background(), WorkspaceActor{ID: "alice", Role: "RESEARCHER"}, r.WorkspaceID, registeredID, 10); err == nil {
		t.Fatal("replacement container accepted")
	}
	if calls != 1 {
		t.Fatalf("expected one inspect call, got %d", calls)
	}
	if _, err := c.InspectWorkspace(context.Background(), "labops-gateway"); err == nil {
		t.Fatal("system container accepted")
	}
}

func TestDockerUnavailableAndFailedOrUnverifiedStopAreErrors(t *testing.T) {
	t.Run("daemon unavailable", func(t *testing.T) {
		c := controllerWith(testRecord(), true, func(*http.Request) (*http.Response, error) { return nil, errors.New("offline") })
		if _, err := c.InspectWorkspace(context.Background(), registeredID); err == nil {
			t.Fatal("expected Docker error")
		}
	})
	t.Run("stop failed", func(t *testing.T) {
		r := testRecord()
		calls := 0
		c := controllerWith(r, true, func(req *http.Request) (*http.Response, error) {
			calls++
			if req.Method == http.MethodPost {
				return jsonResponse(500, "stop failed"), nil
			}
			return jsonResponse(200, inspectJSON(registeredID, r.ContainerName, "running", recordLabels(r))), nil
		})
		if err := c.StopWorkspace(context.Background(), WorkspaceActor{ID: "alice", Role: "RESEARCHER"}, r.WorkspaceID, registeredID, 5); err == nil {
			t.Fatal("failed stop reported success")
		}
		if calls != 2 {
			t.Fatalf("unexpected calls %d", calls)
		}
	})
	t.Run("post stop still running", func(t *testing.T) {
		r := testRecord()
		calls := 0
		c := controllerWith(r, true, func(req *http.Request) (*http.Response, error) {
			calls++
			if req.Method == http.MethodPost {
				return jsonResponse(204, ""), nil
			}
			return jsonResponse(200, inspectJSON(registeredID, r.ContainerName, "running", recordLabels(r))), nil
		})
		if err := c.StopWorkspace(context.Background(), WorkspaceActor{ID: "alice", Role: "RESEARCHER"}, r.WorkspaceID, registeredID, 5); err == nil {
			t.Fatal("unverified stop reported success")
		}
		if calls != 3 {
			t.Fatalf("expected post-stop verification; got %d calls", calls)
		}
	})
}

func TestLegacyStopAndNamePrefixFailClosed(t *testing.T) {
	calls := 0
	c := controllerWith(WorkspaceRecord{}, false, func(*http.Request) (*http.Response, error) {
		calls++
		return jsonResponse(200, `[{"Id":"`+registeredID+`","Names":["/ws-alice"],"Labels":{"labops.managed":"true"}}]`), nil
	})
	if err := c.StopContainer("ws-alice", 5); err == nil {
		t.Fatal("raw name stop was accepted")
	}
	workspaces, err := c.ListWorkspaces(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(workspaces) != 0 {
		t.Fatalf("fake ws-* was reported as managed: %#v", workspaces)
	}
	if calls != 1 {
		t.Fatalf("unexpected Docker calls: %d", calls)
	}
}

func TestWorkspaceListFailsClosedWithoutRegistryBeforeDockerCall(t *testing.T) {
	calls := 0
	c := &Controller{client: &http.Client{Transport: testTransport(func(*http.Request) (*http.Response, error) {
		calls++
		return jsonResponse(http.StatusOK, `[]`), nil
	})}}
	if _, err := c.ListWorkspaces(context.Background()); err == nil {
		t.Fatal("missing trusted registry was reported as an empty successful workspace list")
	}
	if calls != 0 {
		t.Fatalf("Docker should not be queried without trusted registry, got %d calls", calls)
	}
}

func TestAttestWorkspaceReportsOnlyInspectedRunningContainer(t *testing.T) {
	workspaceID := "ws-0123456789"
	labels := map[string]string{
		"labops.managed": "true", "labops.workspace_id": workspaceID,
		"labops.owner_id": "alice", "labops.node_id": "track",
	}
	registry := &reportingRegistry{fakeRegistry: fakeRegistry{found: false}}
	calls := 0
	c := &Controller{localNodeID: "track", registry: registry, client: &http.Client{Transport: testTransport(func(req *http.Request) (*http.Response, error) {
		calls++
		if req.Method != http.MethodGet || req.URL.Path != "/v1.45/containers/"+registeredID+"/json" {
			return nil, errors.New("unexpected Docker inspection request")
		}
		return jsonResponse(http.StatusOK, inspectJSON(registeredID, "labops-"+workspaceID, "running", labels)), nil
	})}}
	record, err := c.AttestWorkspace(context.Background(), workspaceID, registeredID)
	if err != nil {
		t.Fatal(err)
	}
	if record.WorkspaceID != workspaceID || record.ContainerID != registeredID || record.OwnerID != "alice" {
		t.Fatalf("unexpected registration record: %#v", record)
	}
	if len(registry.observations) != 1 || registry.observations[0].State != "running" || registry.observations[0].CPUCores != 2 {
		t.Fatalf("unexpected runtime observations: %#v", registry.observations)
	}
	if registry.observations[0].NetworkMode != "bridge" {
		t.Fatalf("Docker network mode was not included in attestation: %#v", registry.observations[0])
	}
	if calls != 1 {
		t.Fatalf("expected exactly one Docker inspection, got %d", calls)
	}
}

func TestRepositoryEnforcedWorkspaceRejectsNonNoneDockerNetwork(t *testing.T) {
	r := testRecord()
	r.RepositoryPolicyMode = "REPOSITORY_ENFORCED"
	r.NetworkMode = "none"
	r.EgressVerification = "VERIFIED_NO_NETWORK"
	calls, stopCalls := 0, 0
	c := controllerWith(r, true, func(req *http.Request) (*http.Response, error) {
		calls++
		if req.Method == http.MethodPost {
			stopCalls++
			return jsonResponse(http.StatusNoContent, ""), nil
		}
		return jsonResponse(http.StatusOK, inspectJSON(registeredID, r.ContainerName, "running", recordLabels(r))), nil
	})
	if err := c.StopWorkspace(context.Background(), WorkspaceActor{ID: "alice", Role: "RESEARCHER"}, r.WorkspaceID, registeredID, 5); err == nil {
		t.Fatal("bridge-network container accepted under repository-enforced registry record")
	}
	if stopCalls != 0 || calls != 1 {
		t.Fatalf("expected one non-mutating inspect, got calls=%d stopCalls=%d", calls, stopCalls)
	}
}

func TestAttestWorkspaceRejectsPrivilegedAndDockerSocketCandidates(t *testing.T) {
	workspaceID := "ws-0123456789"
	labels := map[string]string{
		"labops.managed": "true", "labops.workspace_id": workspaceID,
		"labops.owner_id": "alice", "labops.node_id": "track",
	}
	for _, tc := range []struct {
		name string
		edit func(string) string
	}{
		{name: "privileged", edit: func(body string) string { return strings.Replace(body, `"Privileged":false`, `"Privileged":true`, 1) }},
		{name: "docker socket", edit: func(body string) string {
			return strings.Replace(body, `"Mounts":[]`, `"Mounts":[{"Source":"/var/run/docker.sock","Destination":"/var/run/docker.sock"}]`, 1)
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			registry := &reportingRegistry{fakeRegistry: fakeRegistry{found: false}}
			body := tc.edit(inspectJSON(registeredID, "labops-"+workspaceID, "running", labels))
			c := &Controller{localNodeID: "track", registry: registry, client: &http.Client{Transport: testTransport(func(*http.Request) (*http.Response, error) {
				return jsonResponse(http.StatusOK, body), nil
			})}}
			if _, err := c.AttestWorkspace(context.Background(), workspaceID, registeredID); err == nil {
				t.Fatal("unsafe runtime candidate was registered")
			}
			if len(registry.observations) != 0 {
				t.Fatalf("unsafe candidate was reported: %#v", registry.observations)
			}
		})
	}
}
