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
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
)

const (
	MinWorkspaceUID       = 20000
	MaxWorkspaceUID       = 59999
	ContainerSSHDPort     = 2222
	DefaultRuntimeRootDir = "/var/lib/labops-agent/runtime"
)

var validOpenSSHPubKeyRegex = regexp.MustCompile(`^(ssh-ed25519|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521|ssh-rsa)\s+[A-Za-z0-9+/=]+(?:\s+[^\r\n]+)?$`)

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
	WorkspaceID        string   `json:"workspace_id"`
	Name               string   `json:"name"`
	MemberID           string   `json:"member_id"`
	MemberUsername     string   `json:"member_username"`
	UID                int      `json:"uid"`
	GID                int      `json:"gid"`
	ImageRef           string   `json:"image_ref"`
	ImageDigest        string   `json:"image_digest"`
	StorageRoot        string   `json:"storage_root"`
	MemberStorageDir   string   `json:"member_storage_dir,omitempty"`
	ContainerMountPath string   `json:"container_mount_path,omitempty"`
	ResourceMode       string   `json:"resource_mode,omitempty"`
	CPUWeight          int      `json:"cpu_weight,omitempty"`
	MemorySafetyMB     int64    `json:"memory_safety_mb,omitempty"`
	SSHPort            int      `json:"ssh_port"`
	VPNBindIP          string   `json:"vpn_bind_ip"`
	CPULimitCores      int      `json:"cpu_limit_cores"`
	RAMLimitMB         int64    `json:"ram_limit_mb"`
	PidsLimit          int64    `json:"pids_limit"`
	GPUUUIDs           []string `json:"gpu_uuids,omitempty"`
	Generation         uint64   `json:"generation"`
	NetworkIsolated    bool     `json:"network_isolated"`
	AuditActor         string   `json:"audit_actor"`
	CorrelationID      string   `json:"correlation_id"`
}

// Controller quản lý giao tiếp Docker Engine qua Unix Socket kèm TrustedRegistry.
type Controller struct {
	socketPath  string
	nodeID      string
	runtimeRoot string
	registry    *TrustedRegistry
	client      *http.Client
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
	runtimeRoot := strings.TrimSpace(os.Getenv("LABOPS_WORKSPACE_RUNTIME_DIR"))
	if runtimeRoot == "" {
		runtimeRoot = DefaultRuntimeRootDir
	}
	tr := &http.Transport{
		DialContext: func(ctx context.Context, proto, addr string) (net.Conn, error) {
			return net.Dial("unix", socketPath)
		},
		DisableKeepAlives: false,
	}
	return &Controller{
		socketPath:  socketPath,
		nodeID:      nodeID,
		runtimeRoot: runtimeRoot,
		registry:    registry,
		client: &http.Client{
			Transport: tr,
			Timeout:   15 * time.Second,
		},
	}
}

// SetRuntimeRoot cấu hình thư mục runtime cô lập (passwd/group/sshd read-only) cho môi trường deploy/test.
func (c *Controller) SetRuntimeRoot(dir string) {
	if strings.TrimSpace(dir) != "" {
		c.runtimeRoot = filepath.Clean(dir)
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
		CpuShares      int64    `json:"CpuShares"`
		PidsLimit      *int64   `json:"PidsLimit"`
		CapDrop        []string `json:"CapDrop"`
		CapAdd         []string `json:"CapAdd"`
		SecurityOpt    []string `json:"SecurityOpt"`
		PidMode        string   `json:"PidMode"`
		IpcMode        string   `json:"IpcMode"`
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
	if strings.EqualFold(raw.HostConfig.IpcMode, "host") {
		violations = append(violations, "HOST_IPC_FORBIDDEN")
	}
	if strings.EqualFold(raw.HostConfig.NetworkMode, "host") {
		violations = append(violations, "HOST_NETWORK_FORBIDDEN")
	}
	if err := validateNonRootUserSpec(raw.Config.User); err != nil {
		violations = append(violations, err.Error())
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
	if raw.HostConfig.NanoCPUs <= 0 && raw.HostConfig.CpuShares <= 0 {
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
		if (dst == "/etc/passwd" || dst == "/etc/group" || dst == "/etc/labops-ssh") && m.RW {
			violations = append(violations, "READONLY_CONFIG_MOUNT_REQUIRED_"+dst)
		}
	}
	return violations
}

func validateNonRootUserSpec(userSpec string) error {
	clean := strings.TrimSpace(userSpec)
	if clean == "" || strings.EqualFold(clean, "root") || clean == "0" || strings.HasPrefix(clean, "0:") {
		return fmt.Errorf("ROOT_USER_FORBIDDEN")
	}
	parts := strings.Split(clean, ":")
	uid, err := strconv.Atoi(strings.TrimSpace(parts[0]))
	if err != nil {
		return fmt.Errorf("NON_NUMERIC_USER_FORBIDDEN")
	}
	gid := uid
	if len(parts) > 1 {
		gid, err = strconv.Atoi(strings.TrimSpace(parts[1]))
		if err != nil {
			return fmt.Errorf("NON_NUMERIC_GROUP_FORBIDDEN")
		}
	}
	if uid < MinWorkspaceUID || uid > MaxWorkspaceUID || gid < MinWorkspaceUID || gid > MaxWorkspaceUID {
		return fmt.Errorf("UNSAFE_UID_GID_FORBIDDEN")
	}
	return nil
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
			if (p.PrivatePort == ContainerSSHDPort || p.PrivatePort == 22) && p.PublicPort > 0 {
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

func (c *Controller) resolveImageID(ctx context.Context, ref string, local bool) (string, error) {
	endpoint := "http://localhost/v1.45/images/" + url.PathEscape(ref) + "/json"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return "", err
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("IMAGE_INSPECT_FAILED: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("IMAGE_INSPECT_FAILED: Docker returned HTTP %d", resp.StatusCode)
	}
	var image struct {
		ID          string   `json:"Id"`
		RepoDigests []string `json:"RepoDigests"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&image); err != nil {
		return "", fmt.Errorf("IMAGE_INSPECT_FAILED: %w", err)
	}
	if !imageDigestRegex.MatchString(image.ID) {
		return "", fmt.Errorf("INVALID_DOCKER_IMAGE_ID: %q", image.ID)
	}
	if local {
		if image.ID != ref {
			return "", fmt.Errorf("IMAGE_ID_MISMATCH: expected %q, got %q", ref, image.ID)
		}
	} else {
		// Docker records repository digests without the optional tag.
		repository, digest, _ := strings.Cut(ref, "@")
		if tag := strings.LastIndex(repository, ":"); tag > strings.LastIndex(repository, "/") {
			repository = repository[:tag]
		}
		if !slices.ContainsFunc(image.RepoDigests, func(repoDigest string) bool {
			repo, value, ok := strings.Cut(repoDigest, "@")
			return ok && value == digest && canonicalImageRepository(repo) == canonicalImageRepository(repository)
		}) {
			return "", fmt.Errorf("IMAGE_MANIFEST_MISMATCH: Docker did not report %q", repository+"@"+digest)
		}
	}
	return image.ID, nil
}

// Docker Hub digests can be reported using either familiar or fully qualified names.
func canonicalImageRepository(repository string) string {
	domain, path, hasSlash := strings.Cut(repository, "/")
	if !hasSlash || (domain != "localhost" && !strings.ContainsAny(domain, ".:")) {
		domain, path = "docker.io", repository
	}
	if domain == "index.docker.io" {
		domain = "docker.io"
	}
	if domain == "docker.io" && !strings.Contains(path, "/") {
		path = "library/" + path
	}
	return domain + "/" + path
}

func (c *Controller) verifyContainerBinding(ctx context.Context, workspaceID string, live *dockerInspectJSON) (*WorkspaceRecord, error) {
	rec, err := c.registry.verifyContainerIdentity(c.nodeID, workspaceID, live)
	if err != nil {
		return nil, err
	}

	expectedImageID := rec.DockerImageID
	if expectedImageID == "" {
		if imageDigestRegex.MatchString(rec.ImageRef) && rec.ImageRef == rec.ImageDigest {
			expectedImageID = rec.ImageRef
		} else if match := registryImageRefRegex.FindStringSubmatch(rec.ImageRef); match != nil && match[1] == rec.ImageDigest {
			// Older records store the manifest digest but have no resolved Docker Image ID.
			expectedImageID, err = c.resolveImageID(ctx, rec.ImageRef, false)
			if err != nil {
				return nil, err
			}
		}
	}
	if !imageDigestRegex.MatchString(expectedImageID) || live.Image != expectedImageID {
		return nil, fmt.Errorf("IMAGE_ID_MISMATCH: expected '%s', got '%s'", expectedImageID, live.Image)
	}
	return rec, nil
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
		rec, err = c.verifyContainerBinding(ctx, "", raw)
		if err != nil {
			return nil, err
		}
		isManaged = true
	} else if !isManaged {
		return nil, fmt.Errorf("UNMANAGED_CONTAINER: container '%s' không thuộc quản lý xác thực của LabOps", name)
	}

	sshPort := 0
	for portKey, bindings := range raw.HostConfig.PortBindings {
		if (strings.HasPrefix(portKey, "2222/") || strings.HasPrefix(portKey, "22/")) && len(bindings) > 0 {
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
	if req.UID < MinWorkspaceUID || req.UID > MaxWorkspaceUID || req.GID < MinWorkspaceUID || req.GID > MaxWorkspaceUID {
		return nil, fmt.Errorf("UNSAFE_UID_GID_REJECTED: workspace bắt buộc UID/GID riêng trong dải %d-%d (nhận %d:%d, cấm root 0 và admin host 1000)", MinWorkspaceUID, MaxWorkspaceUID, req.UID, req.GID)
	}
	if err := c.registry.CheckSSHPortAvailable(c.nodeID, req.SSHPort, req.WorkspaceID); err != nil {
		return nil, err
	}
	isSharedMode := strings.EqualFold(req.ResourceMode, "SHARED") || strings.EqualFold(req.ResourceMode, "SHARED_WEIGHTED")
	if !isSharedMode && req.CPULimitCores <= 0 {
		return nil, fmt.Errorf("RESOURCE_LIMITS_REQUIRED: CPU limit phải > 0")
	}
	if req.RAMLimitMB <= 0 {
		return nil, fmt.Errorf("RESOURCE_LIMITS_REQUIRED: RAM limit phải > 0")
	}
	if req.PidsLimit <= 0 {
		req.PidsLimit = 4096
	}
	localImage := imageDigestRegex.MatchString(req.ImageRef)
	approvedDigest := req.ImageRef
	if !localImage {
		match := registryImageRefRegex.FindStringSubmatch(req.ImageRef)
		if match == nil {
			return nil, fmt.Errorf("MUTABLE_IMAGE_REJECTED: expected an immutable image reference")
		}
		approvedDigest = match[1]
	}
	if req.ImageDigest != "" && req.ImageDigest != approvedDigest {
		return nil, fmt.Errorf("IMAGE_DIGEST_MISMATCH: image reference and digest differ")
	}
	req.ImageDigest = approvedDigest
	imageID, err := c.resolveImageID(ctx, req.ImageRef, localImage)
	if err != nil {
		return nil, err
	}
	hostStorageDir := strings.TrimSpace(req.MemberStorageDir)
	if hostStorageDir == "" {
		hostStorageDir = strings.TrimSpace(req.StorageRoot)
	}
	if hostStorageDir == "" {
		return nil, fmt.Errorf("STORAGE_DIRECTORY_REQUIRED: thiếu member_storage_dir hoặc storage_root")
	}
	if err := ValidateSafeStorageRoot(hostStorageDir); err != nil {
		return nil, err
	}
	containerMount := strings.TrimSpace(req.ContainerMountPath)
	if containerMount == "" {
		containerMount = "/workspace"
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

	var nanoCPUs int64
	var cpuShares int64 = 100
	if isSharedMode {
		nanoCPUs = 0
		if req.CPUWeight > 0 {
			cpuShares = int64(req.CPUWeight)
		}
	} else {
		nanoCPUs = int64(req.CPULimitCores) * 1000000000
		cpuShares = 1024
	}

	rtFiles, rtErr := c.prepareWorkspaceRuntimeDir(req.WorkspaceID, req.MemberUsername, req.UID, req.GID, containerMount)
	if rtErr != nil {
		return nil, rtErr
	}

	binds := []string{
		fmt.Sprintf("%s:%s:rw", hostStorageDir, containerMount),
		fmt.Sprintf("%s:/etc/passwd:ro", rtFiles.passwdPath),
		fmt.Sprintf("%s:/etc/group:ro", rtFiles.groupPath),
		fmt.Sprintf("%s:/etc/labops-ssh:ro", rtFiles.sshDir),
	}

	hostCfg := map[string]any{
		"Privileged":     false,
		"CapDrop":        []string{"ALL"},
		"SecurityOpt":    []string{"no-new-privileges:true"},
		"NetworkMode":    netMode,
		"NanoCpus":       nanoCPUs,
		"CpuShares":      cpuShares,
		"Memory":         req.RAMLimitMB * 1024 * 1024,
		"MemorySwap":     req.RAMLimitMB * 1024 * 1024,
		"PidsLimit":      req.PidsLimit,
		"Binds":          binds,
		"ReadonlyRootfs": false,
	}
	if len(req.GPUUUIDs) > 0 {
		hostCfg["DeviceRequests"] = []map[string]any{
			{
				"Driver":       "nvidia",
				"DeviceIDs":    req.GPUUUIDs,
				"Capabilities": [][]string{{"gpu"}},
			},
		}
	}

	createBody := map[string]any{
		"Image": imageID,
		"User":  fmt.Sprintf("%d:%d", req.UID, req.GID),
		"Env": []string{
			fmt.Sprintf("HOME=%s", containerMount),
			fmt.Sprintf("USER=%s", req.MemberUsername),
			fmt.Sprintf("LOGNAME=%s", req.MemberUsername),
			fmt.Sprintf("LABOPS_WORKSPACE=%s", req.Name),
		},
		"Labels": map[string]string{
			"labops.managed":       "true",
			"labops.workspace_id":  req.WorkspaceID,
			"labops.member_id":     req.MemberID,
			"labops.member":        req.MemberUsername,
			"labops.node_id":       c.nodeID,
			"labops.generation":    strconv.FormatUint(req.Generation, 10),
			"labops.resource_mode": req.ResourceMode,
		},
		"HostConfig": hostCfg,
	}
	if req.SSHPort > 0 && !req.NetworkIsolated {
		portKey := fmt.Sprintf("%d/tcp", ContainerSSHDPort)
		createBody["ExposedPorts"] = map[string]any{portKey: struct{}{}}
		hostCfg["PortBindings"] = map[string]any{
			portKey: []map[string]string{
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

	created, _, err := c.inspectRaw(ctx, createRes.ID)
	if err != nil {
		return nil, fmt.Errorf("CREATED_CONTAINER_INSPECT_FAILED: %w", err)
	}
	if created.ID != createRes.ID || created.Image != imageID {
		return nil, fmt.Errorf("IMAGE_ID_MISMATCH: created container '%s' failed image verification", createRes.ID)
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
		ImageDigest:          req.ImageDigest,
		DockerImageID:        imageID,
		StorageRoot:          hostStorageDir,
		MemberStorageDir:     hostStorageDir,
		ContainerMountPath:   containerMount,
		RuntimeDir:           rtFiles.baseDir,
		GPUUUIDs:             append([]string(nil), req.GPUUUIDs...),
		ResourceMode:         req.ResourceMode,
		CPUWeight:            int(cpuShares),
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
	if live.ID != rec.CanonicalContainerID || live.Image != imageID {
		rec.ObservedState = "NOT_VERIFIED"
		_ = c.registry.UpsertRecord(rec)
		return &rec, fmt.Errorf("IMAGE_ID_MISMATCH: started container failed image verification")
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
	rec, err := c.verifyContainerBinding(ctx, workspaceID, raw)
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
	rec, err := c.verifyContainerBinding(ctx, workspaceID, raw)
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
	ctx := context.Background()
	containerID = strings.TrimSpace(containerID)
	if containerID == "" {
		return fmt.Errorf("container_id không được để trống")
	}

	raw, _, err := c.inspectRaw(ctx, containerID)
	if err != nil {
		return fmt.Errorf("từ chối dừng: không thể xác minh metadata container '%s' (%w)", containerID, err)
	}

	cleanName := strings.TrimPrefix(raw.Name, "/")
	if ProtectedSystemContainers[cleanName] {
		return fmt.Errorf("từ chối can thiệp: container '%s' là dịch vụ hệ thống tối quan trọng", cleanName)
	}

	if c.registry != nil {
		rec, bindErr := c.verifyContainerBinding(ctx, "", raw)
		if bindErr != nil {
			return fmt.Errorf("từ chối can thiệp: %w", bindErr)
		}
		return c.StopWorkspaceContainer(ctx, rec.WorkspaceID, rec.CanonicalContainerID, timeoutSec)
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

	afterRaw, _, afterErr := c.inspectRaw(ctx, raw.ID)
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

type workspaceRuntimeFiles struct {
	baseDir    string
	sshDir     string
	passwdPath string
	groupPath  string
	sshdCfg    string
	authKeys   string
}

func (c *Controller) prepareWorkspaceRuntimeDir(workspaceID, memberUsername string, uid, gid int, containerHome string) (*workspaceRuntimeFiles, error) {
	root := strings.TrimSpace(c.runtimeRoot)
	if root == "" {
		root = DefaultRuntimeRootDir
	}
	cleanWSID := filepath.Base(strings.TrimSpace(workspaceID))
	if cleanWSID == "" || cleanWSID == "." || cleanWSID == ".." || strings.Contains(workspaceID, "/") {
		return nil, fmt.Errorf("INVALID_WORKSPACE_ID: '%s'", workspaceID)
	}
	baseDir := filepath.Join(root, cleanWSID)
	sshDir := filepath.Join(baseDir, "ssh_ro")
	if err := os.MkdirAll(sshDir, 0755); err != nil {
		return nil, fmt.Errorf("RUNTIME_CONFIG_PREPARE_FAILED: %w", err)
	}
	_ = os.Chmod(baseDir, 0755)
	_ = os.Chmod(sshDir, 0755)

	passwdContent := fmt.Sprintf(
		"root:x:0:0:root:/root:/usr/sbin/nologin\n"+
			"sshd:x:100:65534::/run/sshd:/usr/sbin/nologin\n"+
			"nobody:x:65534:65534::/nonexistent:/usr/sbin/nologin\n"+
			"%s:x:%d:%d:LabOps Workspace:%s:/bin/bash\n",
		memberUsername, uid, gid, containerHome,
	)
	groupContent := fmt.Sprintf(
		"root:x:0:\nnogroup:x:65534:\n%s:x:%d:\n",
		memberUsername, gid,
	)
	sshdCfgContent := fmt.Sprintf(
		"Port %d\n"+
			"ListenAddress 0.0.0.0\n"+
			"HostKey /tmp/ssh_host_ed25519_key\n"+
			"PidFile /tmp/sshd.pid\n"+
			"PermitRootLogin no\n"+
			"PasswordAuthentication no\n"+
			"KbdInteractiveAuthentication no\n"+
			"ChallengeResponseAuthentication no\n"+
			"UsePAM no\n"+
			"PubkeyAuthentication yes\n"+
			"AuthorizedKeysFile /etc/labops-ssh/authorized_keys\n"+
			"StrictModes no\n"+
			"AllowUsers %s\n"+
			"AllowTcpForwarding no\n"+
			"X11Forwarding no\n"+
			"Subsystem sftp internal-sftp\n",
		ContainerSSHDPort, memberUsername,
	)

	rf := &workspaceRuntimeFiles{
		baseDir:    baseDir,
		sshDir:     sshDir,
		passwdPath: filepath.Join(baseDir, "passwd"),
		groupPath:  filepath.Join(baseDir, "group"),
		sshdCfg:    filepath.Join(sshDir, "sshd_config"),
		authKeys:   filepath.Join(sshDir, "authorized_keys"),
	}
	if err := writeAtomicFile(rf.passwdPath, []byte(passwdContent), 0644); err != nil {
		return nil, fmt.Errorf("RUNTIME_CONFIG_PREPARE_FAILED: %w", err)
	}
	if err := writeAtomicFile(rf.groupPath, []byte(groupContent), 0644); err != nil {
		return nil, fmt.Errorf("RUNTIME_CONFIG_PREPARE_FAILED: %w", err)
	}
	if err := writeAtomicFile(rf.sshdCfg, []byte(sshdCfgContent), 0644); err != nil {
		return nil, fmt.Errorf("RUNTIME_CONFIG_PREPARE_FAILED: %w", err)
	}
	// Mặc định authorized_keys RỖNG cho tới khi vượt qua authorization gate sau activation lần đầu
	if err := writeAtomicFile(rf.authKeys, []byte(""), 0644); err != nil {
		return nil, fmt.Errorf("RUNTIME_CONFIG_PREPARE_FAILED: %w", err)
	}
	return rf, nil
}

func writeAtomicFile(targetPath string, content []byte, perm os.FileMode) error {
	tmp := targetPath + ".tmp"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, perm)
	if err != nil {
		return err
	}
	if _, err := f.Write(content); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return err
	}
	if err := f.Sync(); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, targetPath)
}

// SyncAuthorizedKeys đồng bộ nguyên tử danh sách public key vào mount read-only của container workspace.
// Khi keys rỗng, tệp được xóa trắng và xác minh đọc lại rỗng (khóa SSH hoàn toàn).
func (c *Controller) SyncAuthorizedKeys(ctx context.Context, workspaceID, memberUsername string, keys []string) (map[string]any, error) {
	if c.registry == nil {
		return nil, ErrRegistryUnavailable
	}
	rec, err := c.registry.GetByWorkspaceID(workspaceID)
	if err != nil {
		return nil, err
	}
	if memberUsername != "" && !strings.EqualFold(rec.MemberUsername, memberUsername) {
		return nil, fmt.Errorf("OWNER_MISMATCH: workspace '%s' thuộc '@%s', không phải '@%s'", workspaceID, rec.MemberUsername, memberUsername)
	}
	runtimeDir := rec.RuntimeDir
	if strings.TrimSpace(runtimeDir) == "" {
		runtimeDir = filepath.Join(c.runtimeRoot, rec.WorkspaceID)
	}
	sshDir := filepath.Join(runtimeDir, "ssh_ro")
	if err := os.MkdirAll(sshDir, 0755); err != nil {
		return nil, fmt.Errorf("SYNC_AUTHORIZED_KEYS_FAILED: %w", err)
	}
	cleanKeys := make([]string, 0, len(keys))
	for _, k := range keys {
		trimmed := strings.TrimSpace(k)
		if trimmed == "" {
			continue
		}
		if strings.ContainsAny(trimmed, "\r\n") || !validOpenSSHPubKeyRegex.MatchString(trimmed) {
			return nil, fmt.Errorf("INVALID_SSH_PUBLIC_KEY: khóa công khai không hợp lệ")
		}
		cleanKeys = append(cleanKeys, trimmed)
	}
	payload := ""
	if len(cleanKeys) > 0 {
		payload = strings.Join(cleanKeys, "\n") + "\n"
	}
	authPath := filepath.Join(sshDir, "authorized_keys")
	if err := writeAtomicFile(authPath, []byte(payload), 0644); err != nil {
		return nil, fmt.Errorf("SYNC_AUTHORIZED_KEYS_FAILED: %w", err)
	}
	readBack, err := os.ReadFile(authPath)
	if err != nil {
		return nil, fmt.Errorf("SYNC_AUTHORIZED_KEYS_READBACK_FAILED: %w", err)
	}
	if string(readBack) != payload {
		return nil, fmt.Errorf("SYNC_AUTHORIZED_KEYS_READBACK_MISMATCH: nội dung đọc lại không khớp")
	}
	return map[string]any{
		"synced":               true,
		"workspace_id":         rec.WorkspaceID,
		"member_username":      rec.MemberUsername,
		"authorized_keys_path": authPath,
		"keys_count":           len(cleanKeys),
		"empty":                len(cleanKeys) == 0,
		"verified_at":          time.Now().UTC().Format(time.RFC3339),
	}, nil
}

// VerifyCheck mô tả kết quả xác minh một điều kiện hạ tầng kèm bằng chứng thực tế.
type VerifyCheck struct {
	Status        string `json:"status"`
	ObservedValue any    `json:"observed_value"`
	ExpectedValue any    `json:"expected_value"`
	Source        string `json:"source"`
	VerifiedAt    string `json:"verified_at"`
	ErrorCode     string `json:"error_code,omitempty"`
	Reason        string `json:"reason,omitempty"`
}

// VerifyWorkspaceRuntime kiểm tra toàn diện container Docker, UID/GID, CPU/RAM/PIDs, cô lập và cổng SSH thực tế.
func (c *Controller) VerifyWorkspaceRuntime(
	ctx context.Context,
	workspaceID string,
	nameOrID string,
	expectedUID int,
	expectedGID int,
	expectedSSHPort int,
	resourceMode string,
	bindIP string,
) (map[string]any, error) {
	now := time.Now().UTC().Format(time.RFC3339)
	target := strings.TrimSpace(nameOrID)
	if target == "" && c.registry != nil && workspaceID != "" {
		if rec, err := c.registry.GetByWorkspaceID(workspaceID); err == nil {
			target = rec.CanonicalContainerID
			if expectedUID == 0 {
				expectedUID = rec.UID
			}
			if expectedGID == 0 {
				expectedGID = rec.GID
			}
			if expectedSSHPort == 0 {
				expectedSSHPort = rec.SSHPort
			}
			if resourceMode == "" {
				resourceMode = rec.ResourceMode
			}
			if bindIP == "" {
				bindIP = rec.VPNBindIP
			}
		}
	}
	if target == "" {
		return nil, fmt.Errorf("CONTAINER_NOT_FOUND: thiếu container_id hoặc workspace không tồn tại")
	}

	raw, _, err := c.inspectRaw(ctx, target)
	if err != nil {
		return map[string]any{
			"verified": false,
			"checks": map[string]VerifyCheck{
				"docker_running": {
					Status: "FAILED", ObservedValue: "MISSING", ExpectedValue: "running",
					Source: "docker_inspect", VerifiedAt: now, ErrorCode: "CONTAINER_NOT_RUNNING", Reason: err.Error(),
				},
			},
		}, nil
	}

	if c.registry != nil {
		if _, bindErr := c.verifyContainerBinding(ctx, workspaceID, raw); bindErr != nil {
			return map[string]any{
				"verified": false,
				"checks": map[string]VerifyCheck{
					"docker_running": {
						Status: "FAILED", ObservedValue: raw.ID, ExpectedValue: workspaceID,
						Source: "trusted_registry", VerifiedAt: now, ErrorCode: "CONTAINER_BINDING_FAILED", Reason: bindErr.Error(),
					},
				},
			}, nil
		}
	}

	violations := ValidateContainerSafety(raw)
	uidExpected := ""
	if expectedUID > 0 && expectedGID > 0 {
		uidExpected = fmt.Sprintf("%d:%d", expectedUID, expectedGID)
	}
	if uidExpected != "" && strings.TrimSpace(raw.Config.User) != uidExpected {
		violations = append(violations, fmt.Sprintf("UID_GID_MISMATCH: expected %s got %s", uidExpected, raw.Config.User))
	}

	dockerRunningCheck := VerifyCheck{
		Status: "VERIFIED", ObservedValue: raw.State.Status, ExpectedValue: "running",
		Source: "docker_inspect", VerifiedAt: now,
	}
	if !raw.State.Running {
		dockerRunningCheck.Status = "FAILED"
		dockerRunningCheck.ErrorCode = "CONTAINER_NOT_RUNNING"
		dockerRunningCheck.Reason = fmt.Sprintf("Container state='%s'", raw.State.Status)
	}

	privCheck := VerifyCheck{
		Status: "VERIFIED", ObservedValue: raw.Config.User, ExpectedValue: uidExpected,
		Source: "docker_inspect", VerifiedAt: now,
	}
	if len(violations) > 0 {
		privCheck.Status = "FAILED"
		privCheck.ErrorCode = "PRIVILEGE_ISOLATION_VIOLATION"
		privCheck.Reason = strings.Join(violations, "; ")
	}

	isShared := strings.EqualFold(resourceMode, "SHARED") || strings.EqualFold(resourceMode, "SHARED_WEIGHTED")
	var pidsVal int64
	if raw.HostConfig.PidsLimit != nil {
		pidsVal = *raw.HostConfig.PidsLimit
	}
	resOK := raw.HostConfig.Memory > 0 && pidsVal > 0 &&
		((isShared && raw.HostConfig.CpuShares > 0) || (!isShared && raw.HostConfig.NanoCPUs > 0))
	resStatus := "VERIFIED"
	resCode := ""
	resReason := ""
	if !resOK {
		resStatus = "FAILED"
		resCode = "RESOURCE_LIMITS_UNENFORCED"
		resReason = fmt.Sprintf("mode=%s nanoCpus=%d cpuShares=%d mem=%d pids=%d",
			resourceMode, raw.HostConfig.NanoCPUs, raw.HostConfig.CpuShares, raw.HostConfig.Memory, pidsVal)
	}
	resCheck := VerifyCheck{
		Status: resStatus,
		ObservedValue: map[string]any{
			"resource_mode": resourceMode,
			"nano_cpus":     raw.HostConfig.NanoCPUs,
			"cpu_shares":    raw.HostConfig.CpuShares,
			"memory_bytes":  raw.HostConfig.Memory,
			"pids_limit":    pidsVal,
		},
		ExpectedValue: map[string]any{"resource_mode": resourceMode, "memory_gt_0": true, "pids_gt_0": true},
		Source:        "docker_hostconfig",
		VerifiedAt:    now,
		ErrorCode:     resCode,
		Reason:        resReason,
	}

	boundPort := 0
	for portKey, bindings := range raw.HostConfig.PortBindings {
		if (strings.HasPrefix(portKey, "2222/") || strings.HasPrefix(portKey, "22/")) && len(bindings) > 0 {
			_, _ = fmt.Sscanf(bindings[0].HostPort, "%d", &boundPort)
		}
	}
	sshStatus := "FAILED"
	sshCode := "SSH_PORT_NOT_BOUND_TO_CONTAINER"
	sshReason := fmt.Sprintf("Container không ánh xạ cổng SSH %d", expectedSSHPort)
	if boundPort > 0 && (expectedSSHPort == 0 || boundPort == expectedSSHPort) {
		probeIP := strings.TrimSpace(bindIP)
		if probeIP == "" || probeIP == "0.0.0.0" {
			probeIP = "127.0.0.1"
		}
		reachable, banner := VerifySSHReachability(probeIP, boundPort, 2*time.Second)
		if reachable {
			sshStatus = "VERIFIED"
			sshCode = ""
			sshReason = banner
		} else {
			sshStatus = "NOT_VERIFIED"
			sshCode = "SSH_CONTAINER_UNREACHABLE"
			sshReason = banner
		}
	}
	sshCheck := VerifyCheck{
		Status:        sshStatus,
		ObservedValue: boundPort,
		ExpectedValue: expectedSSHPort,
		Source:        "docker_port_bindings_and_tcp_banner",
		VerifiedAt:    now,
		ErrorCode:     sshCode,
		Reason:        sshReason,
	}

	allOK := dockerRunningCheck.Status == "VERIFIED" && privCheck.Status == "VERIFIED" && resCheck.Status == "VERIFIED" && sshCheck.Status == "VERIFIED"
	return map[string]any{
		"verified":     allOK,
		"container_id": raw.ID,
		"checks": map[string]VerifyCheck{
			"docker_running":       dockerRunningCheck,
			"privilege_isolation":  privCheck,
			"resource_enforcement": resCheck,
			"ssh_runtime":          sshCheck,
		},
	}, nil
}
