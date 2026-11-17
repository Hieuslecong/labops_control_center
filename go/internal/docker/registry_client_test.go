package docker

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
)

type workspaceRegistryRoundTrip func(*http.Request) (*http.Response, error)

func (f workspaceRegistryRoundTrip) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func registryResponse(status int, body string) *http.Response {
	return &http.Response{
		StatusCode: status,
		Header:     make(http.Header),
		Body:       io.NopCloser(strings.NewReader(body)),
		Request:    &http.Request{},
	}
}

func TestHTTPWorkspaceRegistryAuthenticatesAndReturnsOnlyTrustedRecord(t *testing.T) {
	registry, err := NewHTTPWorkspaceRegistry(
		"https://backend.example/api/v1", strings.Repeat("s", 32), "track",
	)
	if err != nil {
		t.Fatal(err)
	}
	registry.client = &http.Client{Transport: workspaceRegistryRoundTrip(func(req *http.Request) (*http.Response, error) {
		if req.Method != http.MethodGet || req.URL.Path != "/api/v1/internal/workspaces/by-container/"+strings.Repeat("a", 64) {
			return nil, fmt.Errorf("unexpected request: %s %s", req.Method, req.URL.Path)
		}
		if req.Header.Get("Authorization") != "Bearer "+strings.Repeat("s", 32) {
			return nil, fmt.Errorf("missing node credential")
		}
		return registryResponse(http.StatusOK, `{"workspace_id":"ws-a1b2c3d4e5","container_id":"`+strings.Repeat("a", 64)+`","container_name":"ws-alice","owner_id":"alice","node_id":"track","state":"ACTIVE"}`), nil
	})}

	record, found, err := registry.LookupContainer(context.Background(), strings.Repeat("a", 64))
	if err != nil || !found {
		t.Fatalf("LookupContainer() = found %v, err %v", found, err)
	}
	if record.WorkspaceID != "ws-a1b2c3d4e5" || record.OwnerID != "alice" || record.NodeID != "track" {
		t.Fatalf("unexpected trusted record: %#v", record)
	}
}

func TestHTTPWorkspaceRegistryTreatsNotFoundAsUnregisteredAndErrorsClosed(t *testing.T) {
	registry, err := NewHTTPWorkspaceRegistry("http://127.0.0.1:8000/api/v1", strings.Repeat("s", 32), "track")
	if err != nil {
		t.Fatal(err)
	}
	registry.client = &http.Client{Transport: workspaceRegistryRoundTrip(func(*http.Request) (*http.Response, error) {
		return registryResponse(http.StatusNotFound, `{}`), nil
	})}
	if _, found, err := registry.LookupContainer(context.Background(), strings.Repeat("a", 64)); err != nil || found {
		t.Fatalf("not found result = found %v, err %v", found, err)
	}
	registry.client = &http.Client{Transport: workspaceRegistryRoundTrip(func(*http.Request) (*http.Response, error) {
		return registryResponse(http.StatusServiceUnavailable, `{"error":"registry unavailable"}`), nil
	})}
	if _, _, err := registry.LookupContainer(context.Background(), strings.Repeat("a", 64)); err == nil {
		t.Fatal("registry outage was treated as an unregistered container")
	}
}

func TestHTTPWorkspaceRegistryRejectsUnsafeConfigurationAndIDs(t *testing.T) {
	for _, tc := range []struct{ url, token, node string }{
		{"http://192.0.2.1/api/v1", strings.Repeat("s", 32), "track"},
		{"https://backend.example/api/v1", "short", "track"},
		{"https://backend.example/api/v1", strings.Repeat("s", 32), "../track"},
	} {
		if _, err := NewHTTPWorkspaceRegistry(tc.url, tc.token, tc.node); err == nil {
			t.Fatalf("unsafe registry config accepted: %#v", tc)
		}
	}
	registry, err := NewHTTPWorkspaceRegistry("https://backend.example/api/v1", strings.Repeat("s", 32), "track")
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := registry.LookupContainer(context.Background(), "../container"); err == nil {
		t.Fatal("non-canonical container ID accepted")
	}
}

func TestHTTPWorkspaceRegistryRegistersNodeObservedRuntime(t *testing.T) {
	registry, err := NewHTTPWorkspaceRegistry("https://backend.example/api/v1", strings.Repeat("s", 32), "track")
	if err != nil {
		t.Fatal(err)
	}
	containerID := strings.Repeat("c", 64)
	registry.client = &http.Client{Transport: workspaceRegistryRoundTrip(func(req *http.Request) (*http.Response, error) {
		if req.Method != http.MethodPost || req.URL.Path != "/api/v1/internal/workspaces/ws-0123456789/runtime-observation" {
			return nil, fmt.Errorf("unexpected registration request: %s %s", req.Method, req.URL.Path)
		}
		if req.Header.Get("Authorization") != "Bearer "+strings.Repeat("s", 32) {
			return nil, fmt.Errorf("missing node credential")
		}
		var observation RuntimeObservation
		if err := json.NewDecoder(req.Body).Decode(&observation); err != nil {
			return nil, err
		}
		if observation.ContainerID != containerID || observation.State != "running" || observation.NetworkMode != "bridge" || observation.Labels["labops.node_id"] != "track" {
			return nil, fmt.Errorf("unexpected runtime observation: %#v", observation)
		}
		return registryResponse(http.StatusOK, `{"workspace_id":"ws-0123456789","container_id":"`+containerID+`","container_name":"labops-ws-0123456789","owner_id":"alice","node_id":"track","state":"ACTIVE"}`), nil
	})}

	record, err := registry.RecordRuntimeObservation(context.Background(), "ws-0123456789", RuntimeObservation{
		ContainerID:   containerID,
		ContainerName: "labops-ws-0123456789",
		Labels: map[string]string{
			"labops.managed": "true", "labops.workspace_id": "ws-0123456789",
			"labops.owner_id": "alice", "labops.node_id": "track",
		},
		State: "running", NetworkMode: "bridge", Privileged: false, DockerSocketMounted: false,
		CPUCores: 4, RAMBytes: 8 << 30, PidsLimit: 512,
	})
	if err != nil {
		t.Fatal(err)
	}
	if record.ContainerID != containerID || record.NodeID != "track" || record.OwnerID != "alice" {
		t.Fatalf("unexpected registered workspace: %#v", record)
	}
}
