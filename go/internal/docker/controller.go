package docker

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// ProtectedSystemContainers danh sách container hệ thống tuyệt đối không được xóa hoặc can thiệp
var ProtectedSystemContainers = map[string]bool{
	"labops-wg-easy":              true,
	"postgres":                    true,
	"postgresql":                  true,
	"labops-postgres":             true,
	"labops-backend":              true,
	"labops-frontend":             true,
	"wg-easy":                     true,
	"labops-gateway":              true,
	"labops-agent":                true,
	"9router":                     true,
	"labops-control-center":       true,
	"admin_labops_control_center": true,
}

// DockerPortMapping lưu ánh xạ cổng
type DockerPortMapping struct {
	IP          string `json:"ip"`
	PrivatePort int    `json:"private_port"`
	PublicPort  int    `json:"public_port"`
	Type        string `json:"type"`
}

// WorkspaceContainer thông tin workspace container của nghiên cứu viên
type WorkspaceContainer struct {
	ID                 string              `json:"id"`
	Name               string              `json:"name"`
	WorkspaceID        string              `json:"workspace_id,omitempty"`
	MemberUsername     string              `json:"member_username"`
	Image              string              `json:"image"`
	Status             string              `json:"status"` // running, exited, created
	State              string              `json:"state"`
	SSHPort            int                 `json:"ssh_port"`
	Ports              []DockerPortMapping `json:"ports"`
	CreatedAt          string              `json:"created_at"`
	IsManaged          bool                `json:"is_managed"`
	HasDockerSock      bool                `json:"has_docker_socket"` // Vi phạm bảo mật nếu true
	Privileged         bool                `json:"privileged"`        // Vi phạm bảo mật nếu true
	CPULimitCores      int                 `json:"cpu_limit_cores"`
	RAMLimitBytes      int64               `json:"ram_limit_bytes"`
	PidsLimit          int64               `json:"pids_limit"`
	SecurityViolations []string            `json:"security_violations,omitempty"`
}

// ProvisionRequest mô tả cấu hình workspace an toàn cần tạo qua Docker Controller.
type ProvisionRequest struct {
	WorkspaceID     string `json:"workspace_id"`
	Name            string `json:"name"`
	MemberID        string `json:"member_id"`
	MemberUsername  string `json:"member_username"`
	UID             int    `json:"uid"`
	GID             int    `json:"gid"`
	ImageRef        string `json:"image_ref"`
	ImageDigest     string `json:"image_digest"`
	StorageRoot     string `json:"storage_root"`
	SSHPort         int    `json:"ssh_port"`
	VPNBindIP       string `json:"vpn_bind_ip"`
	CPULimitCores   int    `json:"cpu_limit_cores"`
	RAMLimitMB      int64  `json:"ram_limit_mb"`
	PidsLimit       int64  `json:"pids_limit"`
	Generation      uint64 `json:"generation"`
	NetworkIsolated bool   `json:"network_isolated"`
	AuditActor      string `json:"audit_actor"`
	CorrelationID   string `json:"correlation_id"`
}

// Controller quản lý giao tiếp Docker Engine qua Unix Socket kèm TrustedRegistry.
type Controller struct {
	socketPath string
	nodeID     string
	registry   *TrustedRegistry
	client     *http.Client
}

// NewController khởi tạo Docker Controller cơ bản (dùng khi kiểm tra telemetry cục bộ).
func NewController(socketPath string) *Controller {
	return NewControllerWithRegistry(socketPath, "workstation", nil)
}

// NewControllerWithRegistry khởi tạo Docker Controller gắn TrustedWorkspaceRegistry và NodeID.
func NewControllerWithRegistry(socketPath, nodeID string, registry *TrustedRegistry) *Controller {
	if socketPath == "" {
		socketPath = "/var/run/docker.sock"
	}
	if strings.TrimSpace(nodeID) == "" {
		nodeID = "workstation"
	}
	tr := &http.Transport{
		DialContext: func(ctx context.Context, proto, addr string) (net.Conn, error) {
			return net.Dial("unix", socketPath)
		},
		DisableKeepAlives: false,
	}
	return &Controller{
		socketPath: socketPath,
		nodeID:     nodeID,
		registry:   registry,
		client: &http.Client{
			Transport: tr,
			Timeout:   15 * time.Second,
		},
	}
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
	Image   string `json:"Image"`
	Created string `json:"Created"`
	State   struct {
		Status   string `json:"Status"`
		Running  bool   `json:"Running"`
		ExitCode int    `json:"ExitCode"`
	} `json:"State"`
	HostConfig struct {
		Privileged     bool     `json:"Privileged"`
		Memory         int64    `json:"Memory"`
		MemorySwap     int64    `json:"MemorySwap"`
		NanoCPUs       int64    `json:"NanoCpus"`
		PidsLimit      *int64   `json:"PidsLimit"`
		CapDrop        []string `json:"CapDrop"`
		CapAdd         []string `json:"CapAdd"`
		SecurityOpt    []string `json:"SecurityOpt"`
		PidMode        string   `json:"PidMode"`
		NetworkMode    string   `json:"NetworkMode"`
		ReadonlyRootfs bool     `json:"ReadonlyRootfs"`
		PortBindings   map[string][]struct {
			HostIP   string `json:"HostIp"`
			HostPort string `json:"HostPort"`
		} `json:"PortBindings"`
	} `json:"HostConfig"`
	Mounts []struct {
		Type        string `json:"Type"`
		Source      string `json:"Source"`
		Destination string `json:"Destination"`
		RW          bool   `json:"RW"`
	} `json:"Mounts"`
	Config struct {
		Image  string            `json:"Image"`
		Labels map[string]string `json:"Labels"`
		User   string            `json:"User"`
	} `json:"Config"`
}

// ValidateContainerSafety kiểm tra đầy đủ cấu hình sandbox của workspace container.
func ValidateContainerSafety(raw *dockerInspectJSON) []string {
	var violations []string
	if raw == nil {
		return []string{"missing inspect payload"}
	}
	if raw.HostConfig.Privileged {
		violations = append(violations, "PRIVILEGED_FORBIDDEN")
	}
	if strings.EqualFold(raw.HostConfig.PidMode, "host") {
		violations = append(violations, "HOST_PID_FORBIDDEN")
	}
	if strings.EqualFold(raw.HostConfig.NetworkMode, "host") {
		violations = append(violations, "HOST_NETWORK_FORBIDDEN")
	}
	hasCapDropAll := false
	for _, capName := range raw.HostConfig.CapDrop {
		if strings.EqualFold(strings.TrimSpace(capName), "ALL") {
			hasCapDropAll = true
			break
		}
	}
	if !hasCapDropAll {
		violations = append(violations, "CAP_DROP_ALL_REQUIRED")
	}
	forbiddenCaps := map[string]bool{
		"SYS_ADMIN": true, "CAP_SYS_ADMIN": true,
		"SYS_PTRACE": true, "CAP_SYS_PTRACE": true,
		"NET_ADMIN": true, "CAP_NET_ADMIN": true,
		"SYS_MODULE": true, "DAC_OVERRIDE": true,
	}
	for _, capName := range raw.HostConfig.CapAdd {
		if forbiddenCaps[strings.ToUpper(strings.TrimSpace(capName))] {
			violations = append(violations, "FORBIDDEN_CAP_"+strings.ToUpper(capName))
		}
	}
	hasNoNewPrivs := false
	for _, opt := range raw.HostConfig.SecurityOpt {
		if strings.Contains(strings.ToLower(opt), "no-new-privileges") && !strings.Contains(strings.ToLower(opt), "false") {
			hasNoNewPrivs = true
			break
		}
	}
	if !hasNoNewPrivs {
		violations = append(violations, "NO_NEW_PRIVILEGES_REQUIRED")
	}
	if raw.HostConfig.NanoCPUs <= 0 {
		violations = append(violations, "CPU_LIMIT_REQUIRED")
	}
	if raw.HostConfig.Memory <= 0 {
		violations = append(violations, "MEMORY_LIMIT_REQUIRED")
	}
	if raw.HostConfig.PidsLimit == nil || *raw.HostConfig.PidsLimit <= 0 {
		violations = append(violations, "PIDS_LIMIT_REQUIRED")
	}
	for _, m := range raw.Mounts {
		src := strings.TrimSpace(m.Source)
		dst := strings.TrimSpace(m.Destination)
		if strings.Contains(src, "docker.sock") || strings.Contains(dst, "docker.sock") {
			violations = append(violations, "DOCKER_SOCKET_MOUNT_FORBIDDEN")
		}
		if src == "/" || src == "/etc" || src == "/proc" || src == "/sys" || src == "/var/run" || src == "/run" || src == "/dev" || src == "/home" || src == "/boot" || src == "/root" {
			violations = append(violations, "FORBIDDEN_HOST_MOUNT_"+src)
		}
		for _, pref := range forbiddenHostRoots {
			if strings.HasPrefix(src, pref+"/") {
				violations = append(violations, "FORBIDDEN_HOST_PREFIX_"+pref)
			}
		}
	}
	return violations
}

// ListWorkspaces liệt kê các workspace đã được xác minh (chỉ tin label/registry, KHÔNG tin prefix ws- đơn lẻ).
func (c *Controller) ListWorkspaces(ctx context.Context) ([]WorkspaceContainer, error) {
	if c.registry != nil && c.registry.corrupted {
		return nil, ErrRegistryCorrupted
	}

	req, err := http.NewRequestWithContext(ctx, "GET", "http://localhost/v1.45/containers/json?all=1", nil)
	if err != nil {
		return nil, err
	}

	resp, err := c.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("không thể kết nối Docker daemon tại %s: %w", c.socketPath, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("Docker API lỗi: HTTP %d - %s", resp.StatusCode, string(body))
	}

	var rawList []dockerContainerJSON
	if err := json.NewDecoder(resp.Body).Decode(&rawList); err != nil {
		return nil, fmt.Errorf("lỗi giải mã JSON từ Docker: %w", err)
	}

	workspaces := make([]WorkspaceContainer, 0, len(rawList))
	for _, raw := range rawList {
		name := ""
		if len(raw.Names) > 0 {
			name = strings.TrimPrefix(raw.Names[0], "/")
		}
		if ProtectedSystemContainers[name] {
			continue
		}
		if raw.Labels["labops.managed"] != "true" {
			continue
		}

		wsID := strings.TrimSpace(raw.Labels["labops.workspace_id"])
		memberUser := strings.TrimSpace(raw.Labels["labops.member"])

		if c.registry != nil {
			rec, err := c.registry.GetByWorkspaceID(wsID)
			if err != nil || rec.NodeID != c.nodeID || !strings.EqualFold(rec.CanonicalContainerID, raw.ID) || rec.MemberUsername != memberUser {
				continue
			}
		}

		sshPort := 0
		portMappings := make([]DockerPortMapping, 0, len(raw.Ports))
		for _, p := range raw.Ports {
			portMappings = append(portMappings, DockerPortMapping{
				IP:          p.IP,
				PrivatePort: p.PrivatePort,
				PublicPort:  p.PublicPort,
				Type:        p.Type,
			})
			if p.PrivatePort == 22 && p.PublicPort > 0 {
				sshPort = p.PublicPort
			}
		}

		createdTime := time.Unix(raw.Created, 0).UTC().Format(time.RFC3339)

		ws := WorkspaceContainer{
			ID:             raw.ID,
			Name:           name,
			WorkspaceID:    wsID,
			MemberUsername: memberUser,
			Image:          raw.Image,
			Status:         raw.Status,
			State:          raw.State,
			SSHPort:        sshPort,
			Ports:          portMappings,
			CreatedAt:      createdTime,
			IsManaged:      true,
		}
		workspaces = append(workspaces, ws)
	}

	return workspaces, nil
}

func (c *Controller) inspectRaw(ctx context.Context, nameOrID string) (*dockerInspectJSON, int, error) {
	cleanName := strings.TrimPrefix(strings.TrimSpace(nameOrID), "/")
	if cleanName == "" || strings.Contains(cleanName, "/") || strings.Contains(cleanName, "..") {
		return nil, http.StatusBadRequest, fmt.Errorf("INVALID_CONTAINER_REF: '%s'", nameOrID)
	}
	if ProtectedSystemContainers[cleanName] {
		return nil, http.StatusForbidden, fmt.Errorf("thao tác bị từ chối: '%s' là container hệ thống được bảo vệ", cleanName)
	}

	inspectURL := fmt.Sprintf("http://localhost/v1.45/containers/%s/json", url.PathEscape(cleanName))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, inspectURL, nil)
	if err != nil {
		return nil, http.StatusInternalServerError, err
	}

	resp, err := c.client.Do(req)
	if err != nil {
		return nil, http.StatusServiceUnavailable, err
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		return nil, http.StatusNotFound, fmt.Errorf("không tìm thấy container '%s'", cleanName)
	}
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, resp.StatusCode, fmt.Errorf("lỗi inspect Docker: HTTP %d - %s", resp.StatusCode, string(body))
	}

	var raw dockerInspectJSON
	if err := json.NewDecoder(resp.Body).Decode(&raw); err != nil {
		return nil, http.StatusInternalServerError, err
	}
	return &raw, http.StatusOK, nil
}

// InspectWorkspace kiểm tra trạng thái & cấu hình an toàn của một workspace.
func (c *Controller) InspectWorkspace(ctx context.Context, nameOrID string) (*WorkspaceContainer, error) {
	raw, _, err := c.inspectRaw(ctx, nameOrID)
	if err != nil {
		return nil, err
	}

	name := strings.TrimPrefix(raw.Name, "/")
	if ProtectedSystemContainers[name] {
		return nil, fmt.Errorf("thao tác bị từ chối: '%s' là container hệ thống được bảo vệ", name)
	}

	var rec *WorkspaceRecord
	isManaged := raw.Config.Labels["labops.managed"] == "true"
	if c.registry != nil {
		rec, err = c.registry.VerifyContainerBinding(c.nodeID, "", raw)
		if err != nil {
			return nil, err
		}
		isManaged = true
	} else if !isManaged {
		return nil, fmt.Errorf("UNMANAGED_CONTAINER: container '%s' không thuộc quản lý xác thực của LabOps", name)
	}

	sshPort := 0
	for portKey, bindings := range raw.HostConfig.PortBindings {
		if strings.HasPrefix(portKey, "22/") && len(bindings) > 0 {
			var p int
			_, _ = fmt.Sscanf(bindings[0].HostPort, "%d", &p)
			if p > 0 {
				sshPort = p
			}
		}
	}

	hasDockerSock := false
	for _, m := range raw.Mounts {
		if strings.Contains(m.Source, "docker.sock") || strings.Contains(m.Destination, "docker.sock") {
			hasDockerSock = true
			break
		}
	}

	cpuCores := 0
	if raw.HostConfig.NanoCPUs > 0 {
		cpuCores = int(raw.HostConfig.NanoCPUs / 1000000000)
	}
	var pidsLim int64
	if raw.HostConfig.PidsLimit != nil {
		pidsLim = *raw.HostConfig.PidsLimit
	}

	memberUser := raw.Config.Labels["labops.member"]
	wsID := raw.Config.Labels["labops.workspace_id"]
	if rec != nil {
		memberUser = rec.MemberUsername
		wsID = rec.WorkspaceID
	}

	violations := ValidateContainerSafety(raw)

	return &WorkspaceContainer{
		ID:                 raw.ID,
		Name:               name,
		WorkspaceID:        wsID,
		MemberUsername:     memberUser,
		Image:              raw.Image,
		Status:             raw.State.Status,
		State:              raw.State.Status,
		SSHPort:            sshPort,
		CreatedAt:          raw.Created,
		IsManaged:          isManaged,
		HasDockerSock:      hasDockerSock,
		Privileged:         raw.HostConfig.Privileged,
		CPULimitCores:      cpuCores,
		RAMLimitBytes:      raw.HostConfig.Memory,
		PidsLimit:          pidsLim,
		SecurityViolations: violations,
	}, nil
}

// ProvisionWorkspace khởi tạo workspace container chuẩn zero-trust qua TrustedRegistry (không bao giờ ghi đè container cũ).
func (c *Controller) ProvisionWorkspace(ctx context.Context, req ProvisionRequest) (*WorkspaceRecord, error) {
	if c.registry == nil {
		return nil, ErrRegistryUnavailable
	}
	if err := c.registry.checkHealthyLocked(); err != nil {
		return nil, err
	}
	if strings.TrimSpace(req.WorkspaceID) == "" || !safeWorkspaceNameRegex.MatchString(req.Name) || ProtectedSystemContainers[req.Name] {
		return nil, fmt.Errorf("INVALID_WORKSPACE_SPEC: workspace_id hoặc name '%s' không hợp lệ", req.Name)
	}
	if strings.TrimSpace(req.MemberID) == "" || strings.TrimSpace(req.MemberUsername) == "" {
		return nil, fmt.Errorf("INVALID_MEMBER: thiếu member_id hoặc member_username")
	}
	if req.UID < 1000 || req.GID < 1000 {
		return nil, fmt.Errorf("ROOT_USER_FORBIDDEN: workspace bắt buộc UID/GID >= 1000 (nhận %d:%d)", req.UID, req.GID)
	}
	if req.CPULimitCores <= 0 || req.RAMLimitMB <= 0 || req.PidsLimit <= 0 {
		return nil, fmt.Errorf("RESOURCE_LIMITS_REQUIRED: CPU, RAM và PIDs limits phải > 0")
	}
	if !IsImmutableImageRef(req.ImageRef) && !IsImmutableImageRef(req.ImageDigest) {
		return nil, fmt.Errorf("MUTABLE_IMAGE_REJECTED: bắt buộc image pinned theo @sha256: digest")
	}
	if req.ImageDigest == "" && strings.Contains(req.ImageRef, "@sha256:") {
		parts := strings.SplitN(req.ImageRef, "@", 2)
		req.ImageDigest = parts[1]
	}
	if !IsImmutableImageRef(req.ImageDigest) {
		return nil, fmt.Errorf("INVALID_IMAGE_DIGEST: digest '%s' không phải chuẩn sha256:<64hex>", req.ImageDigest)
	}
	if err := ValidateSafeStorageRoot(req.StorageRoot); err != nil {
		return nil, err
	}

	// KHÔNG BAO GIỜ tự xóa container cũ khi trùng tên!
	if existing, status, _ := c.inspectRaw(ctx, req.Name); existing != nil || status == http.StatusOK {
		return nil, fmt.Errorf("ERR_CONTAINER_EXISTS_PRESERVED: container '%s' đã tồn tại; giữ nguyên dữ liệu cũ, không tự động thay thế", req.Name)
	}

	bindIP := strings.TrimSpace(req.VPNBindIP)
	if bindIP == "" || bindIP == "0.0.0.0" {
		bindIP = "127.0.0.1"
	}
	netMode := "bridge"
	if req.NetworkIsolated {
		netMode = "none"
	}
	if req.Generation == 0 {
		req.Generation = 1
	}

	createBody := map[string]any{
		"Image": req.ImageRef,
		"User":  fmt.Sprintf("%d:%d", req.UID, req.GID),
		"Labels": map[string]string{
			"labops.managed":      "true",
			"labops.workspace_id": req.WorkspaceID,
			"labops.member_id":    req.MemberID,
			"labops.member":       req.MemberUsername,
			"labops.node_id":      c.nodeID,
			"labops.generation":   strconv.FormatUint(req.Generation, 10),
		},
		"HostConfig": map[string]any{
			"Privileged":     false,
			"CapDrop":        []string{"ALL"},
			"SecurityOpt":    []string{"no-new-privileges:true"},
			"NetworkMode":    netMode,
			"NanoCpus":       int64(req.CPULimitCores) * 1000000000,
			"Memory":         req.RAMLimitMB * 1024 * 1024,
			"MemorySwap":     req.RAMLimitMB * 1024 * 1024,
			"PidsLimit":      req.PidsLimit,
			"Binds":          []string{fmt.Sprintf("%s:/workspace:rw", req.StorageRoot)},
			"ReadonlyRootfs": false,
		},
	}
	if req.SSHPort > 0 && !req.NetworkIsolated {
		createBody["ExposedPorts"] = map[string]any{"22/tcp": struct{}{}}
		hc := createBody["HostConfig"].(map[string]any)
		hc["PortBindings"] = map[string]any{
			"22/tcp": []map[string]string{
				{"HostIp": bindIP, "HostPort": strconv.Itoa(req.SSHPort)},
			},
		}
	}

	bodyBytes, err := json.Marshal(createBody)
	if err != nil {
		return nil, err
	}

	createURL := fmt.Sprintf("http://localhost/v1.45/containers/create?name=%s", url.QueryEscape(req.Name))
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, createURL, bytes.NewReader(bodyBytes))
	if err != nil {
		return nil, err
	}
	httpReq.Header.Set("Content-Type", "application/json")

	resp, err := c.client.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("DOCKER_CREATE_FAILED: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusConflict {
		return nil, fmt.Errorf("ERR_CONTAINER_EXISTS_PRESERVED: Docker daemon báo trùng tên container '%s'", req.Name)
	}
	if resp.StatusCode != http.StatusCreated && resp.StatusCode != http.StatusOK {
		errBody, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("DOCKER_CREATE_FAILED: HTTP %d - %s", resp.StatusCode, strings.TrimSpace(string(errBody)))
	}

	var createRes struct {
		ID string `json:"Id"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&createRes); err != nil || !IsCanonicalContainerID(createRes.ID) {
		return nil, fmt.Errorf("INVALID_CREATED_CONTAINER_ID: phản hồi ID không hợp lệ (%q)", createRes.ID)
	}

	rec := WorkspaceRecord{
		WorkspaceID:          req.WorkspaceID,
		Name:                 req.Name,
		MemberID:             req.MemberID,
		MemberUsername:       req.MemberUsername,
		UID:                  req.UID,
		GID:                  req.GID,
		NodeID:               c.nodeID,
		CanonicalContainerID: strings.ToLower(createRes.ID),
		ImageRef:             req.ImageRef,
		ImageDigest:          strings.ToLower(req.ImageDigest),
		StorageRoot:          req.StorageRoot,
		SSHPort:              req.SSHPort,
		VPNBindIP:            bindIP,
		CPULimitCores:        req.CPULimitCores,
		RAMLimitMB:           req.RAMLimitMB,
		PidsLimit:            req.PidsLimit,
		Generation:           req.Generation,
		DesiredState:         "ACTIVE",
		ObservedState:        "PARTIAL_PROVISIONING",
		AuditActor:           req.AuditActor,
		CorrelationID:        req.CorrelationID,
	}
	if err := c.registry.UpsertRecord(rec); err != nil {
		return nil, err
	}

	// Start container theo canonical ID
	startURL := fmt.Sprintf("http://localhost/v1.45/containers/%s/start", url.PathEscape(rec.CanonicalContainerID))
	startReq, _ := http.NewRequestWithContext(ctx, http.MethodPost, startURL, nil)
	startResp, startErr := c.client.Do(startReq)
	if startErr != nil {
		rec.ObservedState = "PARTIAL_PROVISIONING"
		_ = c.registry.UpsertRecord(rec)
		return &rec, fmt.Errorf("DOCKER_START_FAILED: %w", startErr)
	}
	startBody, _ := io.ReadAll(startResp.Body)
	startResp.Body.Close()
	if startResp.StatusCode != http.StatusNoContent && startResp.StatusCode != http.StatusNotModified {
		rec.ObservedState = "FAILED"
		_ = c.registry.UpsertRecord(rec)
		return &rec, fmt.Errorf("DOCKER_START_FAILED: HTTP %d - %s", startResp.StatusCode, strings.TrimSpace(string(startBody)))
	}

	// Hậu kiểm cấu hình an toàn thực tế từ Docker daemon trước khi đánh dấu ACTIVE
	live, _, inspectErr := c.inspectRaw(ctx, rec.CanonicalContainerID)
	if inspectErr != nil {
		rec.ObservedState = "NOT_VERIFIED"
		_ = c.registry.UpsertRecord(rec)
		return &rec, fmt.Errorf("POST_START_INSPECT_FAILED: %w", inspectErr)
	}
	if violations := ValidateContainerSafety(live); len(violations) > 0 {
		rec.ObservedState = "FAILED_SAFETY_POLICY"
		_ = c.registry.UpsertRecord(rec)
		return &rec, fmt.Errorf("CONTAINER_SAFETY_VIOLATION: %s", strings.Join(violations, ", "))
	}
	if !live.State.Running {
		rec.ObservedState = "FAILED"
		_ = c.registry.UpsertRecord(rec)
		return &rec, fmt.Errorf("CONTAINER_NOT_RUNNING: state='%s'", live.State.Status)
	}

	rec.ObservedState = "ACTIVE"
	rec.LastVerifiedAt = time.Now().UTC().Format(time.RFC3339)
	if err := c.registry.UpsertRecord(rec); err != nil {
		return nil, err
	}
	return &rec, nil
}

// StopWorkspaceContainer dừng an toàn một container được xác minh qua WorkspaceID + Canonical 64-hex ContainerID.
func (c *Controller) StopWorkspaceContainer(ctx context.Context, workspaceID, expectedContainerID string, timeoutSec int) error {
	if c.registry == nil {
		return ErrRegistryUnavailable
	}
	expectedContainerID = strings.TrimSpace(strings.ToLower(expectedContainerID))
	if !IsCanonicalContainerID(expectedContainerID) {
		return fmt.Errorf("INVALID_CANONICAL_ID: StopWorkspaceContainer yêu cầu container_id 64-hex chuẩn")
	}

	raw, _, err := c.inspectRaw(ctx, expectedContainerID)
	if err != nil {
		return err
	}
	rec, err := c.registry.VerifyContainerBinding(c.nodeID, workspaceID, raw)
	if err != nil {
		return err
	}

	stopURL := fmt.Sprintf("http://localhost/v1.45/containers/%s/stop?t=%d", url.PathEscape(rec.CanonicalContainerID), timeoutSec)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, stopURL, nil)
	if err != nil {
		return err
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return fmt.Errorf("lỗi gửi request dừng container tới Docker daemon: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusNotModified && resp.StatusCode != http.StatusNoContent {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("Docker daemon trả về lỗi (code %d): %s", resp.StatusCode, strings.TrimSpace(string(body)))
	}

	afterRaw, _, afterErr := c.inspectRaw(ctx, rec.CanonicalContainerID)
	if afterErr != nil || afterRaw == nil || afterRaw.State.Running || afterRaw.State.Status == "running" {
		return fmt.Errorf("POST_STOP_VERIFICATION_FAILED: container '%s' vẫn đang chạy hoặc không xác minh được", rec.CanonicalContainerID)
	}

	rec.DesiredState = "STOPPED"
	rec.ObservedState = "STOPPED"
	rec.LastVerifiedAt = time.Now().UTC().Format(time.RFC3339)
	_ = c.registry.UpsertRecord(*rec)
	return nil
}

// RemoveStoppedWorkspaceContainer xóa một container đã dừng hoàn toàn sau khi xác minh Canonical ID & TrustedRegistry.
func (c *Controller) RemoveStoppedWorkspaceContainer(ctx context.Context, workspaceID, expectedContainerID string) error {
	if c.registry == nil {
		return ErrRegistryUnavailable
	}
	expectedContainerID = strings.TrimSpace(strings.ToLower(expectedContainerID))
	if !IsCanonicalContainerID(expectedContainerID) {
		return fmt.Errorf("INVALID_CANONICAL_ID: bắt buộc 64-hex container ID")
	}

	raw, _, err := c.inspectRaw(ctx, expectedContainerID)
	if err != nil {
		return err
	}
	rec, err := c.registry.VerifyContainerBinding(c.nodeID, workspaceID, raw)
	if err != nil {
		return err
	}
	if raw.State.Running || raw.State.Status == "running" {
		return fmt.Errorf("CONTAINER_STILL_RUNNING: phải dừng và xác minh thoát tiến trình trước khi xóa container")
	}

	delURL := fmt.Sprintf("http://localhost/v1.45/containers/%s", url.PathEscape(rec.CanonicalContainerID))
	req, err := http.NewRequestWithContext(ctx, http.MethodDelete, delURL, nil)
	if err != nil {
		return err
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusNoContent && resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusNotFound {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("DOCKER_REMOVE_FAILED: HTTP %d - %s", resp.StatusCode, strings.TrimSpace(string(body)))
	}

	rec.DesiredState = "DELETED"
	rec.ObservedState = "DELETED"
	rec.LastVerifiedAt = time.Now().UTC().Format(time.RFC3339)
	return c.registry.UpsertRecord(*rec)
}

// StopContainer dừng một container qua Docker Unix Socket có kiểm tra TrustedRegistry & ghim Canonical ID (AT-07).
func (c *Controller) StopContainer(containerID string, timeoutSec int) error {
	containerID = strings.TrimSpace(containerID)
	if containerID == "" {
		return fmt.Errorf("container_id không được để trống")
	}

	raw, _, err := c.inspectRaw(context.Background(), containerID)
	if err != nil {
		return fmt.Errorf("từ chối dừng: không thể xác minh metadata container '%s' (%w)", containerID, err)
	}

	cleanName := strings.TrimPrefix(raw.Name, "/")
	if ProtectedSystemContainers[cleanName] {
		return fmt.Errorf("từ chối can thiệp: container '%s' là dịch vụ hệ thống tối quan trọng", cleanName)
	}

	if c.registry != nil {
		rec, bindErr := c.registry.VerifyContainerBinding(c.nodeID, "", raw)
		if bindErr != nil {
			return fmt.Errorf("từ chối can thiệp: %w", bindErr)
		}
		return c.StopWorkspaceContainer(context.Background(), rec.WorkspaceID, rec.CanonicalContainerID, timeoutSec)
	}

	if raw.Config.Labels["labops.managed"] != "true" {
		return fmt.Errorf("từ chối can thiệp: container '%s' không có xác nhận labops.managed=true", cleanName)
	}
	if !IsCanonicalContainerID(raw.ID) {
		return fmt.Errorf("từ chối can thiệp: ID container không hợp lệ '%s'", raw.ID)
	}

	stopURL := fmt.Sprintf("http://localhost/v1.45/containers/%s/stop?t=%d", url.PathEscape(raw.ID), timeoutSec)
	req, err := http.NewRequest(http.MethodPost, stopURL, nil)
	if err != nil {
		return err
	}

	resp, err := c.client.Do(req)
	if err != nil {
		return fmt.Errorf("lỗi gửi request dừng container tới Docker daemon: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusNotModified && resp.StatusCode != http.StatusNoContent {
		if resp.StatusCode == http.StatusNotFound {
			return fmt.Errorf("không tìm thấy container '%s'", raw.ID)
		}
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("Docker daemon trả về lỗi (code %d): %s", resp.StatusCode, strings.TrimSpace(string(body)))
	}

	afterRaw, _, afterErr := c.inspectRaw(context.Background(), raw.ID)
	if afterErr == nil && afterRaw != nil && (afterRaw.State.Running || afterRaw.State.Status == "running") {
		return fmt.Errorf("xác minh thất bại: container '%s' vẫn đang ở trạng thái running sau lệnh dừng", raw.ID)
	}

	return nil
}

// VerifySSHReachability kiểm tra cổng SSH container có thực sự phản hồi hay không
func VerifySSHReachability(host string, port int, timeout time.Duration) (bool, string) {
	if port <= 0 {
		return false, "Cổng SSH chưa được cấp phát"
	}
	target := net.JoinHostPort(host, fmt.Sprintf("%d", port))
	conn, err := net.DialTimeout("tcp", target, timeout)
	if err != nil {
		return false, fmt.Sprintf("Không thể kết nối TCP %s: %v", target, err)
	}
	defer conn.Close()

	_ = conn.SetReadDeadline(time.Now().Add(timeout))
	buf := make([]byte, 256)
	n, err := conn.Read(buf)
	if err != nil {
		return false, fmt.Sprintf("Không đọc được banner SSH từ %s: %v", target, err)
	}
	banner := strings.TrimSpace(string(buf[:n]))
	if !strings.HasPrefix(banner, "SSH-") {
		return false, fmt.Sprintf("Phản hồi trên port %d không phải giao thức SSH: %s", port, banner)
	}
	return true, banner
}
