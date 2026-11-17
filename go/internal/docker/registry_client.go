package docker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

// HTTPWorkspaceRegistry reads backend-owned runtime identity for the local node.
// Its URL is explicit; non-TLS remote URLs and implicit host endpoints are rejected.
type HTTPWorkspaceRegistry struct {
	baseURL string
	token   string
	nodeID  string
	client  *http.Client
}

type RuntimeObservation struct {
	ContainerID         string            `json:"container_id"`
	ContainerName       string            `json:"container_name"`
	Labels              map[string]string `json:"labels"`
	State               string            `json:"state"`
	NetworkMode         string            `json:"network_mode"`
	Privileged          bool              `json:"privileged"`
	DockerSocketMounted bool              `json:"docker_socket_mounted"`
	CPUCores            int               `json:"cpu_cores"`
	RAMBytes            int64             `json:"ram_bytes"`
	PidsLimit           int               `json:"pids_limit"`
}

type WorkspaceRuntimeReporter interface {
	RecordRuntimeObservation(ctx context.Context, workspaceID string, observation RuntimeObservation) (WorkspaceRecord, error)
}

var workspaceIDPattern = regexp.MustCompile(`^ws-[a-f0-9]{10}$`)

func NewHTTPWorkspaceRegistry(baseURL, token, nodeID string) (*HTTPWorkspaceRegistry, error) {
	parsed, err := url.Parse(strings.TrimSpace(baseURL))
	if err != nil || parsed == nil || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, errors.New("workspace registry URL is invalid")
	}
	if parsed.Scheme != "https" {
		ip := net.ParseIP(parsed.Hostname())
		if parsed.Scheme != "http" || ip == nil || !ip.IsLoopback() {
			return nil, errors.New("workspace registry requires HTTPS except for a loopback test endpoint")
		}
	}
	if len(token) < 32 {
		return nil, errors.New("workspace registry node credential must contain at least 32 characters")
	}
	if !validRegistryNodeID(nodeID) {
		return nil, errors.New("workspace registry node ID is invalid")
	}
	base := strings.TrimRight(parsed.String(), "/")
	if !strings.HasSuffix(strings.TrimRight(parsed.Path, "/"), "/api/v1") {
		return nil, errors.New("workspace registry URL must include the /api/v1 base path")
	}
	return &HTTPWorkspaceRegistry{
		baseURL: base,
		token:   token,
		nodeID:  nodeID,
		client: &http.Client{
			Timeout:       3 * time.Second,
			Transport:     &http.Transport{Proxy: nil},
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
	}, nil
}

func validRegistryNodeID(value string) bool {
	if value == "" || len(value) > 63 {
		return false
	}
	for i, r := range value {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || (i > 0 && r == '-') {
			continue
		}
		return false
	}
	return true
}

func (r *HTTPWorkspaceRegistry) LookupContainer(ctx context.Context, canonicalContainerID string) (WorkspaceRecord, bool, error) {
	if !isCanonicalContainerID(canonicalContainerID) {
		return WorkspaceRecord{}, false, errors.New("canonical container ID required")
	}
	endpoint := r.baseURL + "/internal/workspaces/by-container/" + canonicalContainerID
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return WorkspaceRecord{}, false, fmt.Errorf("create workspace registry request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+r.token)
	resp, err := r.client.Do(req)
	if err != nil {
		return WorkspaceRecord{}, false, fmt.Errorf("workspace registry unavailable: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		return WorkspaceRecord{}, false, nil
	}
	if resp.StatusCode != http.StatusOK {
		return WorkspaceRecord{}, false, fmt.Errorf("workspace registry returned HTTP %d", resp.StatusCode)
	}
	var record WorkspaceRecord
	decoder := json.NewDecoder(io.LimitReader(resp.Body, 16<<10))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&record); err != nil {
		return WorkspaceRecord{}, false, fmt.Errorf("decode workspace registry record: %w", err)
	}
	if record.WorkspaceID == "" || record.ContainerID != canonicalContainerID || record.OwnerID == "" ||
		record.NodeID != r.nodeID || record.State != "ACTIVE" || record.ContainerName == "" {
		return WorkspaceRecord{}, false, errors.New("workspace registry returned an invalid or mismatched record")
	}
	if record.RepositoryPolicyMode == "REPOSITORY_ENFORCED" &&
		(record.NetworkMode != "none" || record.EgressVerification != "VERIFIED_NO_NETWORK") {
		return WorkspaceRecord{}, false, errors.New("workspace registry returned an unverified repository egress policy")
	}
	return record, true, nil
}

func (r *HTTPWorkspaceRegistry) RecordRuntimeObservation(ctx context.Context, workspaceID string, observation RuntimeObservation) (WorkspaceRecord, error) {
	if !workspaceIDPattern.MatchString(workspaceID) {
		return WorkspaceRecord{}, errors.New("workspace ID is invalid")
	}
	if !isCanonicalContainerID(observation.ContainerID) || observation.ContainerName == "" ||
		observation.State != "running" || observation.Privileged || observation.DockerSocketMounted ||
		observation.CPUCores <= 0 || observation.RAMBytes <= 0 || observation.PidsLimit <= 0 {
		return WorkspaceRecord{}, errors.New("runtime observation is invalid or unsafe")
	}
	if observation.NetworkMode == "" || len(observation.NetworkMode) > 128 || strings.ContainsAny(observation.NetworkMode, "\r\n\x00") {
		return WorkspaceRecord{}, errors.New("runtime observation has no valid Docker network mode")
	}
	if observation.Labels["labops.managed"] != "true" ||
		observation.Labels["labops.workspace_id"] != workspaceID ||
		observation.Labels["labops.node_id"] != r.nodeID ||
		observation.Labels["labops.owner_id"] == "" {
		return WorkspaceRecord{}, errors.New("runtime observation labels do not match the local node and workspace")
	}
	endpoint := r.baseURL + "/internal/workspaces/" + url.PathEscape(workspaceID) + "/runtime-observation"
	body, err := json.Marshal(observation)
	if err != nil {
		return WorkspaceRecord{}, fmt.Errorf("encode runtime observation: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, strings.NewReader(string(body)))
	if err != nil {
		return WorkspaceRecord{}, fmt.Errorf("create runtime registration request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+r.token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := r.client.Do(req)
	if err != nil {
		return WorkspaceRecord{}, fmt.Errorf("workspace runtime registration unavailable: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return WorkspaceRecord{}, fmt.Errorf("workspace runtime registration returned HTTP %d", resp.StatusCode)
	}
	var record WorkspaceRecord
	decoder := json.NewDecoder(io.LimitReader(resp.Body, 16<<10))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&record); err != nil {
		return WorkspaceRecord{}, fmt.Errorf("decode workspace runtime registration: %w", err)
	}
	if record.WorkspaceID != workspaceID || record.ContainerID != observation.ContainerID ||
		record.ContainerName != observation.ContainerName || record.OwnerID != observation.Labels["labops.owner_id"] ||
		record.NodeID != r.nodeID || record.State != "ACTIVE" {
		return WorkspaceRecord{}, errors.New("workspace runtime registration returned a mismatched record")
	}
	if record.RepositoryPolicyMode == "REPOSITORY_ENFORCED" &&
		(observation.NetworkMode != "none" || record.NetworkMode != "none" || record.EgressVerification != "VERIFIED_NO_NETWORK") {
		return WorkspaceRecord{}, errors.New("workspace repository egress policy was not verified")
	}
	return record, nil
}
