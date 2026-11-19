package gateway

import (
	"sync"
	"time"

	pb "github.com/Hieuslecong/labops_control_center/go/proto/labops/v1"
)

// AllowedActions định nghĩa danh sách các hành động điều khiển từ xa được phép thực thi
var AllowedActions = map[string]bool{
	"INSPECT_HOST":                    true,
	"INSPECT_GPUS":                    true,
	"VERIFY_PROCESS_PID":              true,
	"PROVISION_DOCKER_WORKSPACE":      true,
	"STOP_DOCKER_WORKSPACE":           true,
	"ARCHIVE_REMOVE_DOCKER_WORKSPACE": true,
	"SYNC_SSH_AUTHORIZED_KEYS":        true,
	"INSPECT_WIREGUARD_PEERS":         true,
	"COLLECT_TELEMETRY":               true,
	"TERMINATE_PROCESS":               true,
	"KILL_PROCESS":                    true,
	"REPO_DECISION":                   true,
	"REPO_DELIVER":                    true,
	"LIST_STORAGE_POOLS":              true,
	"INSPECT_STORAGE_POOL":            true,
	"VALIDATE_STORAGE_ALLOCATION":     true,
	"PROVISION_MEMBER_STORAGE":        true,
	"GET_RESOURCE_PROFILE":            true,
}

// NodeSession đại diện cho một phiên kết nối gRPC streaming của Compute Node
type NodeSession struct {
	mu              sync.RWMutex
	SessionID       string
	Generation      uint64
	NodeID          string
	Hostname        string
	Role            string
	AgentVersion    string
	OSInfo          string
	KernelVersion   string
	ManagementIP    string
	GPUCount        int32
	Status          string // "ONLINE", "DEGRADED", "OFFLINE", "SUPERSEDED"
	DegradedReasons []string
	ConnectedAt     time.Time
	LastHeartbeat   time.Time
	UptimeSeconds   int64
	superseded      bool

	// gRPC Stream & Communication
	stream          pb.NodeAgentGatewayService_TunnelServer
	streamMu        sync.Mutex
	pendingCommands map[string]chan *pb.CommandResponse
	pendingMu       sync.Mutex

	// Cached Telemetry
	cachedHost      *pb.HostTelemetry
	cachedGPU       *pb.GPUTelemetry
	cachedProcess   *pb.ProcessTelemetry
	cachedWorkspace *pb.WorkspaceTelemetry
	telemetryMu     sync.RWMutex
}

// NodeSummary tóm tắt thông tin node phục vụ Control Plane API
type NodeSummary struct {
	NodeID          string    `json:"node_id"`
	Hostname        string    `json:"hostname"`
	Role            string    `json:"role"`
	Status          string    `json:"status"`
	AgentVersion    string    `json:"agent_version"`
	ManagementIP    string    `json:"management_ip"`
	GPUCount        int32     `json:"gpu_count"`
	SessionID       string    `json:"session_id,omitempty"`
	Generation      uint64    `json:"generation,omitempty"`
	ConnectedAt     time.Time `json:"connected_at"`
	LastHeartbeat   time.Time `json:"last_heartbeat"`
	DegradedReasons []string  `json:"degraded_reasons,omitempty"`
}

// DispatchRequest yêu cầu gửi lệnh tới node từ Controller
type DispatchRequest struct {
	NodeID               string            `json:"node_id"`
	Action               string            `json:"action"`
	Parameters           map[string]string `json:"parameters"`
	TimeoutSeconds       int64             `json:"timeout_seconds"`
	ActorID              string            `json:"actor_id"`
	RequestID            string            `json:"request_id,omitempty"`
	IdempotencyKey       string            `json:"idempotency_key"`
	AuthorizationContext string            `json:"authorization_context,omitempty"`
}

// DispatchResponse kết quả trả về cho Controller
type DispatchResponse struct {
	Success      bool   `json:"success"`
	CommandID    string `json:"command_id"`
	NodeID       string `json:"node_id"`
	Status       string `json:"status"`
	ErrorCode    string `json:"error_code,omitempty"`
	ErrorMessage string `json:"error_message,omitempty"`
	Result       any    `json:"result,omitempty"`
	DurationMs   int64  `json:"duration_ms"`
}

