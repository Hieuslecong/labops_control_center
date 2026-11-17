package docker

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

var ProtectedSystemContainers = map[string]bool{
	"labops-wg-easy": true, "postgres": true, "postgresql": true, "wg-easy": true,
	"labops-gateway": true, "labops-agent": true, "labops-control-center": true,
	"admin_labops_control_center": true,
}

type DockerPortMapping struct {
	IP          string `json:"ip"`
	PrivatePort int    `json:"private_port"`
	PublicPort  int    `json:"public_port"`
	Type        string `json:"type"`
}

type WorkspaceContainer struct {
	ID                   string              `json:"id"`
	WorkspaceID          string              `json:"workspace_id,omitempty"`
	NodeID               string              `json:"node_id,omitempty"`
	Name                 string              `json:"name"`
	MemberUsername       string              `json:"member_username"`
	Image                string              `json:"image"`
	Status               string              `json:"status"`
	State                string              `json:"state"`
	SSHPort              int                 `json:"ssh_port"`
	Ports                []DockerPortMapping `json:"ports"`
	CreatedAt            string              `json:"created_at"`
	IsManaged            bool                `json:"is_managed"`
	HasDockerSock        bool                `json:"has_docker_socket"`
	Privileged           bool                `json:"privileged"`
	CPULimitCores        int                 `json:"cpu_limit_cores"`
	RAMLimitBytes        int64               `json:"ram_limit_bytes"`
	PidsLimit            int                 `json:"pids_limit"`
	RuntimeLabels        map[string]string   `json:"-"`
	NetworkMode          string              `json:"network_mode,omitempty"`
	RepositoryPolicyMode string              `json:"repository_policy_mode,omitempty"`
	EgressVerification   string              `json:"egress_verification,omitempty"`
}

// WorkspaceRecord is the trusted control-plane registration. Docker names and labels
// are only consistency checks; neither can create ownership.
type WorkspaceRecord struct {
	WorkspaceID          string `json:"workspace_id"`
	ContainerID          string `json:"container_id"`
	ContainerName        string `json:"container_name"`
	OwnerID              string `json:"owner_id"`
	NodeID               string `json:"node_id"`
	State                string `json:"state"`
	RepositoryPolicyMode string `json:"repository_policy_mode,omitempty"`
	NetworkMode          string `json:"observed_network_mode,omitempty"`
	EgressVerification   string `json:"egress_verification,omitempty"`
}

type WorkspaceRegistry interface {
	LookupContainer(ctx context.Context, canonicalContainerID string) (WorkspaceRecord, bool, error)
}

type WorkspaceActor struct {
	ID   string
	Role string
}

type Controller struct {
	socketPath  string
	localNodeID string
	registry    WorkspaceRegistry
	client      *http.Client
}

func NewController(socketPath string) *Controller {
	return NewControllerWithRegistry(socketPath, "", nil)
}

func NewControllerWithRegistry(socketPath, localNodeID string, registry WorkspaceRegistry) *Controller {
	if socketPath == "" {
		socketPath = "/var/run/docker.sock"
	}
	tr := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", socketPath)
	}}
	return &Controller{socketPath: socketPath, localNodeID: strings.TrimSpace(localNodeID), registry: registry,
		client: &http.Client{Transport: tr, Timeout: 10 * time.Second}}
}

type dockerContainerJSON struct {
	ID      string            `json:"Id"`
	Names   []string          `json:"Names"`
	Image   string            `json:"Image"`
	State   string            `json:"State"`
	Status  string            `json:"Status"`
	Created int64             `json:"Created"`
	Labels  map[string]string `json:"Labels"`
	Ports   []struct {
		IP          string `json:"IP"`
		PrivatePort int    `json:"PrivatePort"`
		PublicPort  int    `json:"PublicPort"`
		Type        string `json:"Type"`
	} `json:"Ports"`
}

type dockerInspectJSON struct {
	ID      string `json:"Id"`
	Name    string `json:"Name"`
	Created string `json:"Created"`
	State   struct {
		Status   string `json:"Status"`
		Running  bool   `json:"Running"`
		ExitCode int    `json:"ExitCode"`
	} `json:"State"`
	HostConfig struct {
		Privileged   bool   `json:"Privileged"`
		NetworkMode  string `json:"NetworkMode"`
		Memory       int64  `json:"Memory"`
		NanoCPUs     int64  `json:"NanoCpus"`
		PidsLimit    int    `json:"PidsLimit"`
		PortBindings map[string][]struct {
			HostIP   string `json:"HostIp"`
			HostPort string `json:"HostPort"`
		} `json:"PortBindings"`
	} `json:"HostConfig"`
	Mounts []struct {
		Source      string `json:"Source"`
		Destination string `json:"Destination"`
	} `json:"Mounts"`
	Config struct {
		Labels map[string]string `json:"Labels"`
		User   string            `json:"User"`
	} `json:"Config"`
}

func (c *Controller) ListWorkspaces(ctx context.Context) ([]WorkspaceContainer, error) {
	if c.registry == nil {
		return nil, errors.New("trusted workspace registry unavailable")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://docker/v1.45/containers/json?all=1", nil)
	if err != nil {
		return nil, err
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("Docker API unavailable: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("Docker API returned HTTP %d", resp.StatusCode)
	}
	var entries []dockerContainerJSON
	if err := json.NewDecoder(io.LimitReader(resp.Body, 8<<20)).Decode(&entries); err != nil {
		return nil, fmt.Errorf("decode Docker container list: %w", err)
	}
	out := make([]WorkspaceContainer, 0, len(entries))
	for _, raw := range entries {
		if !isCanonicalContainerID(raw.ID) {
			continue
		}
		record, ok, err := c.lookupTrusted(ctx, raw.ID, raw.Labels, firstName(raw.Names), "")
		if err != nil {
			return nil, err
		}
		if !ok {
			continue
		}
		name := strings.TrimPrefix(firstName(raw.Names), "/")
		ports := make([]DockerPortMapping, 0, len(raw.Ports))
		sshPort := 0
		for _, p := range raw.Ports {
			ports = append(ports, DockerPortMapping{IP: p.IP, PrivatePort: p.PrivatePort, PublicPort: p.PublicPort, Type: p.Type})
			if p.PrivatePort == 22 && p.PublicPort > 0 {
				sshPort = p.PublicPort
			}
		}
		out = append(out, WorkspaceContainer{ID: raw.ID, WorkspaceID: record.WorkspaceID, NodeID: record.NodeID,
			Name: name, MemberUsername: record.OwnerID, Image: raw.Image, Status: raw.Status, State: raw.State,
			SSHPort: sshPort, Ports: ports, CreatedAt: time.Unix(raw.Created, 0).UTC().Format(time.RFC3339), IsManaged: true,
			RepositoryPolicyMode: record.RepositoryPolicyMode, EgressVerification: record.EgressVerification})
	}
	return out, nil
}

func (c *Controller) InspectWorkspace(ctx context.Context, nameOrID string) (*WorkspaceContainer, error) {
	clean := strings.TrimPrefix(nameOrID, "/")
	if ProtectedSystemContainers[clean] {
		return nil, fmt.Errorf("protected system container")
	}
	if clean == "" || strings.ContainsAny(clean, "/\\\x00") {
		return nil, errors.New("invalid container reference")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://docker/v1.45/containers/"+url.PathEscape(clean)+"/json", nil)
	if err != nil {
		return nil, err
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("Docker API unavailable: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("Docker inspect returned HTTP %d", resp.StatusCode)
	}
	var raw dockerInspectJSON
	if err := json.NewDecoder(io.LimitReader(resp.Body, 2<<20)).Decode(&raw); err != nil {
		return nil, err
	}
	if !isCanonicalContainerID(raw.ID) {
		return nil, errors.New("Docker returned a non-canonical container ID")
	}
	if isCanonicalContainerID(clean) && !strings.EqualFold(clean, raw.ID) {
		return nil, errors.New("container ID changed during inspect")
	}
	name := strings.TrimPrefix(raw.Name, "/")
	if ProtectedSystemContainers[name] {
		return nil, fmt.Errorf("protected system container")
	}
	record, managed, err := c.lookupTrusted(ctx, raw.ID, raw.Config.Labels, name, raw.HostConfig.NetworkMode)
	if err != nil {
		return nil, err
	}
	out := &WorkspaceContainer{ID: raw.ID, Name: name, Status: raw.State.Status, State: raw.State.Status,
		CreatedAt: raw.Created, IsManaged: managed, HasDockerSock: hasDockerSocket(raw.Mounts),
		Privileged: raw.HostConfig.Privileged, CPULimitCores: ceilingCPUCount(raw.HostConfig.NanoCPUs),
		RAMLimitBytes: raw.HostConfig.Memory, PidsLimit: raw.HostConfig.PidsLimit, RuntimeLabels: raw.Config.Labels,
		NetworkMode: raw.HostConfig.NetworkMode}
	if managed {
		out.WorkspaceID, out.MemberUsername, out.NodeID = record.WorkspaceID, record.OwnerID, record.NodeID
		out.RepositoryPolicyMode, out.EgressVerification = record.RepositoryPolicyMode, record.EgressVerification
	}
	for key, bindings := range raw.HostConfig.PortBindings {
		if strings.HasPrefix(key, "22/") && len(bindings) > 0 {
			_, _ = fmt.Sscanf(bindings[0].HostPort, "%d", &out.SSHPort)
		}
	}
	return out, nil
}

func ceilingCPUCount(nanoCPUs int64) int {
	if nanoCPUs <= 0 {
		return 0
	}
	return int((nanoCPUs + 999_999_999) / 1_000_000_000)
}

// AttestWorkspace reports an observed Docker runtime to the trusted backend registry.
// Docker labels only identify the candidate; the Backend must independently bind them
// to a pending workspace record before it stores ownership.
func (c *Controller) AttestWorkspace(ctx context.Context, workspaceID, containerID string) (WorkspaceRecord, error) {
	if !workspaceIDPattern.MatchString(workspaceID) || !isCanonicalContainerID(containerID) {
		return WorkspaceRecord{}, errors.New("valid workspace ID and canonical container ID are required")
	}
	if c.registry == nil {
		return WorkspaceRecord{}, errors.New("trusted workspace registry unavailable")
	}
	reporter, ok := c.registry.(WorkspaceRuntimeReporter)
	if !ok {
		return WorkspaceRecord{}, errors.New("workspace registry does not support runtime registration")
	}
	observed, err := c.InspectWorkspace(ctx, containerID)
	if err != nil {
		return WorkspaceRecord{}, fmt.Errorf("inspect workspace candidate: %w", err)
	}
	if observed.ID != containerID || observed.Name != "labops-"+workspaceID || observed.State != "running" ||
		observed.Privileged || observed.HasDockerSock || observed.CPULimitCores <= 0 ||
		observed.RAMLimitBytes <= 0 || observed.PidsLimit <= 0 {
		return WorkspaceRecord{}, errors.New("workspace candidate runtime is unsafe or does not match the requested workspace")
	}
	labels := observed.RuntimeLabels
	if labels["labops.managed"] != "true" || labels["labops.workspace_id"] != workspaceID ||
		labels["labops.node_id"] != c.localNodeID || labels["labops.owner_id"] == "" || c.localNodeID == "" {
		return WorkspaceRecord{}, errors.New("workspace candidate ownership labels are incomplete or mismatched")
	}
	return reporter.RecordRuntimeObservation(ctx, workspaceID, RuntimeObservation{
		ContainerID: observed.ID, ContainerName: observed.Name, Labels: labels,
		State: observed.State, Privileged: observed.Privileged, DockerSocketMounted: observed.HasDockerSock,
		NetworkMode: observed.NetworkMode, CPUCores: observed.CPULimitCores, RAMBytes: observed.RAMLimitBytes, PidsLimit: observed.PidsLimit,
	})
}

func (c *Controller) lookupTrusted(ctx context.Context, id string, labels map[string]string, name, observedNetworkMode string) (WorkspaceRecord, bool, error) {
	if c.registry == nil {
		return WorkspaceRecord{}, false, nil
	}
	record, found, err := c.registry.LookupContainer(ctx, id)
	if err != nil {
		return WorkspaceRecord{}, false, fmt.Errorf("workspace registry lookup failed: %w", err)
	}
	if !found || record.State != "ACTIVE" || record.WorkspaceID == "" || record.OwnerID == "" || record.NodeID == "" || record.ContainerID != id {
		return WorkspaceRecord{}, false, nil
	}
	if c.localNodeID == "" || record.NodeID != c.localNodeID {
		return WorkspaceRecord{}, false, nil
	}
	if labels["labops.managed"] != "true" || labels["labops.workspace_id"] != record.WorkspaceID ||
		labels["labops.owner_id"] != record.OwnerID || labels["labops.node_id"] != record.NodeID {
		return WorkspaceRecord{}, false, nil
	}
	if record.ContainerName == "" || strings.TrimPrefix(record.ContainerName, "/") != name || ProtectedSystemContainers[name] {
		return WorkspaceRecord{}, false, nil
	}
	policyMode := record.RepositoryPolicyMode
	if policyMode == "" {
		policyMode = "UNENFORCED" // Legacy workspace records carry no egress attestation.
	}
	if policyMode != "UNENFORCED" && policyMode != "REPOSITORY_ENFORCED" {
		return WorkspaceRecord{}, false, nil
	}
	if policyMode == "REPOSITORY_ENFORCED" &&
		(record.NetworkMode != "none" || record.EgressVerification != "VERIFIED_NO_NETWORK" ||
			(observedNetworkMode != "" && observedNetworkMode != "none")) {
		return WorkspaceRecord{}, false, nil
	}
	return record, true, nil
}

// StopContainer is retained for compatibility but fails closed: a raw Docker ID is
// insufficient authorization. Use StopWorkspace with an authenticated actor.
func (c *Controller) StopContainer(containerID string, timeoutSec int) error {
	return errors.New("workspace stop requires authenticated workspace authorization")
}

func (c *Controller) StopWorkspace(ctx context.Context, actor WorkspaceActor, workspaceID, containerID string, timeoutSec int) error {
	if actor.ID == "" || (actor.Role != "ADMIN" && actor.Role != "RESEARCHER") {
		return errors.New("workspace action is unauthorized")
	}
	if c.registry == nil {
		return errors.New("trusted workspace registry unavailable")
	}
	if !isCanonicalContainerID(containerID) {
		return errors.New("canonical container ID required")
	}
	before, err := c.inspectCanonical(ctx, containerID)
	if err != nil {
		return err
	}
	if !before.IsManaged || before.WorkspaceID != workspaceID {
		return errors.New("container is not the registered workspace")
	}
	if actor.Role != "ADMIN" && actor.ID != before.MemberUsername {
		return errors.New("workspace owner mismatch")
	}
	if timeoutSec < 0 || timeoutSec > 300 {
		return errors.New("invalid stop timeout")
	}
	if before.State != "running" {
		return fmt.Errorf("workspace is not running (state=%s)", before.State)
	}
	// Revalidate the control-plane binding after Docker inspect and immediately
	// before the mutating call. The immutable Docker ID prevents name reuse, while
	// this second registry read prevents stale ownership/state from authorizing stop.
	latest, found, err := c.registry.LookupContainer(ctx, containerID)
	if err != nil {
		return fmt.Errorf("revalidate workspace authorization: %w", err)
	}
	if !found || latest.State != "ACTIVE" || latest.ContainerID != before.ID ||
		latest.WorkspaceID != before.WorkspaceID || latest.OwnerID != before.MemberUsername ||
		latest.NodeID != before.NodeID || latest.ContainerName != before.Name ||
		latest.NodeID != c.localNodeID || latest.WorkspaceID != workspaceID {
		return errors.New("workspace authorization changed before stop")
	}
	if actor.Role != "ADMIN" && actor.ID != latest.OwnerID {
		return errors.New("workspace owner mismatch")
	}
	endpoint := fmt.Sprintf("http://docker/v1.45/containers/%s/stop?t=%d", url.PathEscape(containerID), timeoutSec)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, nil)
	if err != nil {
		return err
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return fmt.Errorf("Docker stop failed: %w", err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusNoContent && resp.StatusCode != http.StatusNotModified {
		return fmt.Errorf("Docker stop returned HTTP %d", resp.StatusCode)
	}
	after, err := c.inspectCanonical(ctx, containerID)
	if err != nil {
		return fmt.Errorf("post-stop verification failed: %w", err)
	}
	if after.ID != before.ID || after.WorkspaceID != before.WorkspaceID || after.NodeID != before.NodeID || after.MemberUsername != before.MemberUsername {
		return errors.New("workspace identity changed during stop")
	}
	if after.State == "running" {
		return errors.New("Docker stop returned success but container is still running")
	}
	return nil
}

func (c *Controller) inspectCanonical(ctx context.Context, id string) (*WorkspaceContainer, error) {
	if !isCanonicalContainerID(id) {
		return nil, errors.New("canonical container ID required")
	}
	return c.InspectWorkspace(ctx, id)
}

func isCanonicalContainerID(id string) bool {
	if len(id) != 64 {
		return false
	}
	_, err := hex.DecodeString(id)
	return err == nil
}
func firstName(names []string) string {
	if len(names) == 0 {
		return ""
	}
	return strings.TrimPrefix(names[0], "/")
}
func hasDockerSocket(mounts []struct {
	Source      string `json:"Source"`
	Destination string `json:"Destination"`
}) bool {
	for _, m := range mounts {
		if strings.Contains(m.Source, "docker.sock") || strings.Contains(m.Destination, "docker.sock") {
			return true
		}
	}
	return false
}

func VerifySSHReachability(host string, port int, timeout time.Duration) (bool, string) {
	if port <= 0 {
		return false, "SSH port is not assigned"
	}
	conn, err := net.DialTimeout("tcp", net.JoinHostPort(host, fmt.Sprintf("%d", port)), timeout)
	if err != nil {
		return false, fmt.Sprintf("SSH connection failed: %v", err)
	}
	defer conn.Close()
	_ = conn.SetReadDeadline(time.Now().Add(timeout))
	buf := make([]byte, 256)
	n, err := conn.Read(buf)
	if err != nil {
		return false, fmt.Sprintf("SSH banner read failed: %v", err)
	}
	banner := strings.TrimSpace(string(buf[:n]))
	if !strings.HasPrefix(banner, "SSH-") {
		return false, "endpoint did not return an SSH banner"
	}
	return true, banner
}
