package docker

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"time"
)

// ProtectedSystemContainers danh sách container hệ thống tuyệt đối không được xóa hoặc can thiệp
var ProtectedSystemContainers = map[string]bool{
	"labops-wg-easy":              true,
	"postgres":                    true,
	"postgresql":                  true,
	"wg-easy":                     true,
	"labops-gateway":              true,
	"labops-agent":                true,
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
	ID             string              `json:"id"`
	Name           string              `json:"name"`
	MemberUsername string              `json:"member_username"`
	Image          string              `json:"image"`
	Status         string              `json:"status"` // running, exited, created
	State          string              `json:"state"`
	SSHPort        int                 `json:"ssh_port"`
	Ports          []DockerPortMapping `json:"ports"`
	CreatedAt      string              `json:"created_at"`
	IsManaged      bool                `json:"is_managed"`
	HasDockerSock  bool                `json:"has_docker_socket"` // Vi phạm bảo mật nếu true
	Privileged     bool                `json:"privileged"`        // Vi phạm bảo mật nếu true
	CPULimitCores  int                 `json:"cpu_limit_cores"`
	RAMLimitBytes  int64               `json:"ram_limit_bytes"`
}

// Controller quản lý giao tiếp Docker Engine qua Unix Socket
type Controller struct {
	socketPath string
	client     *http.Client
}

// NewController khởi tạo Docker Controller
func NewController(socketPath string) *Controller {
	if socketPath == "" {
		socketPath = "/var/run/docker.sock"
	}
	tr := &http.Transport{
		DialContext: func(ctx context.Context, proto, addr string) (net.Conn, error) {
			return net.Dial("unix", socketPath)
		},
		DisableKeepAlives: false,
	}
	return &Controller{
		socketPath: socketPath,
		client: &http.Client{
			Transport: tr,
			Timeout:   10 * time.Second,
		},
	}
}

// dockerContainerJSON struct parse từ API Docker /containers/json
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

// dockerInspectJSON struct parse từ API Docker /containers/{id}/json
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
		Privileged   bool  `json:"Privileged"`
		Memory       int64 `json:"Memory"`
		NanoCPUs     int64 `json:"NanoCpus"`
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

// ListWorkspaces liệt kê tất cả các workspace containers (loại bỏ container hệ thống)
func (c *Controller) ListWorkspaces(ctx context.Context) ([]WorkspaceContainer, error) {
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

		// Bảo vệ container hệ thống tuyệt đối
		if ProtectedSystemContainers[name] {
			continue
		}

		// Chỉ lấy các container do LabOps quản lý (có label hoặc prefix ws-)
		isManaged := raw.Labels["labops.managed"] == "true" || strings.HasPrefix(name, "ws-")
		if !isManaged {
			continue
		}

		memberUser := raw.Labels["labops.member"]
		if memberUser == "" && strings.HasPrefix(name, "ws-") {
			memberUser = strings.TrimPrefix(name, "ws-")
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
			ID:             raw.ID[:12],
			Name:           name,
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

// InspectWorkspace kiểm tra chi tiết trạng thái thực tế của một workspace
func (c *Controller) InspectWorkspace(ctx context.Context, nameOrID string) (*WorkspaceContainer, error) {
	// Kiểm tra bảo vệ hệ thống
	cleanName := strings.TrimPrefix(nameOrID, "/")
	if ProtectedSystemContainers[cleanName] {
		return nil, fmt.Errorf("thao tác bị từ chối: '%s' là container hệ thống được bảo vệ", cleanName)
	}

	url := fmt.Sprintf("http://localhost/v1.45/containers/%s/json", nameOrID)
	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return nil, err
	}

	resp, err := c.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		return nil, fmt.Errorf("không tìm thấy container '%s'", nameOrID)
	}
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("lỗi inspect Docker: HTTP %d - %s", resp.StatusCode, string(body))
	}

	var raw dockerInspectJSON
	if err := json.NewDecoder(resp.Body).Decode(&raw); err != nil {
		return nil, err
	}

	name := strings.TrimPrefix(raw.Name, "/")
	memberUser := raw.Config.Labels["labops.member"]
	if memberUser == "" && strings.HasPrefix(name, "ws-") {
		memberUser = strings.TrimPrefix(name, "ws-")
	}

	sshPort := 0
	for portKey, bindings := range raw.HostConfig.PortBindings {
		if strings.HasPrefix(portKey, "22/") && len(bindings) > 0 {
			var p int
			fmt.Sscanf(bindings[0].HostPort, "%d", &p)
			if p > 0 {
				sshPort = p
			}
		}
	}

	// Kiểm tra vi phạm bảo mật (Mount docker.sock)
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

	return &WorkspaceContainer{
		ID:             raw.ID[:12],
		Name:           name,
		MemberUsername: memberUser,
		Status:         raw.State.Status,
		State:          raw.State.Status,
		SSHPort:        sshPort,
		CreatedAt:      raw.Created,
		IsManaged:      true,
		HasDockerSock:  hasDockerSock,
		Privileged:     raw.HostConfig.Privileged,
		CPULimitCores:  cpuCores,
		RAMLimitBytes:  raw.HostConfig.Memory,
	}, nil
}

// StopContainer dừng một container qua Docker Unix Socket có kiểm tra safeguard nghiêm ngặt (AT-07)
func (c *Controller) StopContainer(containerID string, timeoutSec int) error {
	containerID = strings.TrimSpace(containerID)
	if containerID == "" {
		return fmt.Errorf("container_id không được để trống")
	}

	// 1. Inspect bắt buộc để kiểm tra metadata trước khi dừng (fail-closed)
	ws, err := c.InspectWorkspace(context.Background(), containerID)
	if err != nil {
		return fmt.Errorf("từ chối dừng: không thể xác minh metadata container '%s' (%w)", containerID, err)
	}
	if ws == nil {
		return fmt.Errorf("từ chối dừng: container '%s' không tồn tại", containerID)
	}

	cleanName := strings.TrimPrefix(ws.Name, "/")
	// Chặn mọi container hệ thống quan trọng
	if ProtectedSystemContainers[cleanName] {
		return fmt.Errorf("từ chối can thiệp: container '%s' là dịch vụ hệ thống tối quan trọng", cleanName)
	}

	// Chặn các container không phải là workspace do LabOps quản lý
	if !ws.IsManaged || (!strings.HasPrefix(cleanName, "ws-") && ws.MemberUsername == "") {
		return fmt.Errorf("từ chối can thiệp: container '%s' không thuộc danh mục workspace do LabOps quản lý", cleanName)
	}

	url := fmt.Sprintf("http://localhost/containers/%s/stop?t=%d", containerID, timeoutSec)
	req, err := http.NewRequest(http.MethodPost, url, nil)
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
			return fmt.Errorf("không tìm thấy container '%s'", containerID)
		}
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("Docker daemon trả về lỗi (code %d): %s", resp.StatusCode, strings.TrimSpace(string(body)))
	}

	// 2. Xác minh trạng thái thực tế sau khi stop (runtime verification)
	afterWs, afterErr := c.InspectWorkspace(context.Background(), containerID)
	if afterErr == nil && afterWs != nil {
		if afterWs.State == "running" {
			return fmt.Errorf("xác minh thất bại: container '%s' vẫn đang ở trạng thái running sau lệnh dừng", containerID)
		}
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

	// Đọc SSH banner ban đầu (ví dụ: SSH-2.0-OpenSSH...)
	conn.SetReadDeadline(time.Now().Add(timeout))
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
