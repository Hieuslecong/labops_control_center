package gateway

import (
	"context"
	"encoding/json"
	"fmt"
	"sync"
	"time"

	pb "github.com/Hieuslecong/labops_control_center/go/proto/labops/v1"
)

// SessionManager quản lý tập trung trạng thái các kết nối gRPC của Compute Nodes
type SessionManager struct {
	mu       sync.RWMutex
	sessions map[string]*NodeSession
}

func NewSessionManager() *SessionManager {
	sm := &SessionManager{
		sessions: make(map[string]*NodeSession),
	}
	// Tự động kiểm tra node timeout mỗi 5 giây
	go sm.monitorHeartbeats()
	return sm
}

func (sm *SessionManager) RegisterSession(hello *pb.NodeHello, stream pb.NodeAgentGatewayService_TunnelServer) *NodeSession {
	sm.mu.Lock()
	defer sm.mu.Unlock()

	now := time.Now()
	session := &NodeSession{
		NodeID:          hello.NodeId,
		Hostname:        hello.Hostname,
		Role:            hello.Role,
		AgentVersion:    hello.AgentVersion,
		OSInfo:          hello.OsInfo,
		KernelVersion:   hello.KernelVersion,
		ManagementIP:    hello.ManagementIp,
		GPUCount:        hello.GpuCount,
		Status:          "ONLINE",
		ConnectedAt:     now,
		LastHeartbeat:   now,
		stream:          stream,
		pendingCommands: make(map[string]chan *pb.CommandResponse),
	}

	sm.sessions[hello.NodeId] = session
	return session
}

func (sm *SessionManager) RemoveSession(nodeID string) {
	sm.mu.Lock()
	defer sm.mu.Unlock()

	if sess, exists := sm.sessions[nodeID]; exists {
		sess.Status = "OFFLINE"
		// Hủy mọi pending commands
		sess.pendingMu.Lock()
		for cmdID, ch := range sess.pendingCommands {
			close(ch)
			delete(sess.pendingCommands, cmdID)
		}
		sess.pendingMu.Unlock()
	}
}

func (sm *SessionManager) GetSession(nodeID string) (*NodeSession, bool) {
	sm.mu.RLock()
	defer sm.mu.RUnlock()
	sess, exists := sm.sessions[nodeID]
	return sess, exists
}

func (sm *SessionManager) ListNodes() []*NodeSummary {
	sm.mu.RLock()
	defer sm.mu.RUnlock()

	list := make([]*NodeSummary, 0, len(sm.sessions))
	for _, sess := range sm.sessions {
		list = append(list, &NodeSummary{
			NodeID:          sess.NodeID,
			Hostname:        sess.Hostname,
			Role:            sess.Role,
			Status:          sess.Status,
			AgentVersion:    sess.AgentVersion,
			ManagementIP:    sess.ManagementIP,
			GPUCount:        sess.GPUCount,
			ConnectedAt:     sess.ConnectedAt,
			LastHeartbeat:   sess.LastHeartbeat,
			DegradedReasons: sess.DegradedReasons,
		})
	}
	return list
}

func (sm *SessionManager) UpdateHeartbeat(nodeID string, hb *pb.NodeHeartbeat) {
	sm.mu.RLock()
	sess, exists := sm.sessions[nodeID]
	sm.mu.RUnlock()

	if !exists {
		return
	}

	sess.streamMu.Lock()
	defer sess.streamMu.Unlock()

	sess.LastHeartbeat = time.Now()
	sess.UptimeSeconds = hb.UptimeSeconds

	if hb.Health != nil {
		switch hb.Health.State {
		case pb.NodeHealthStatus_HEALTHY:
			sess.Status = "ONLINE"
			sess.DegradedReasons = nil
		case pb.NodeHealthStatus_DEGRADED:
			sess.Status = "DEGRADED"
			sess.DegradedReasons = hb.Health.DegradedReasons
		case pb.NodeHealthStatus_UNHEALTHY:
			sess.Status = "UNHEALTHY"
			sess.DegradedReasons = hb.Health.DegradedReasons
		}
	} else {
		sess.Status = "ONLINE"
	}
}

func (sm *SessionManager) UpdateHostTelemetry(nodeID string, t *pb.HostTelemetry) {
	if sess, ok := sm.GetSession(nodeID); ok {
		sess.telemetryMu.Lock()
		sess.cachedHost = t
		sess.telemetryMu.Unlock()
	}
}

func (sm *SessionManager) UpdateGPUTelemetry(nodeID string, t *pb.GPUTelemetry) {
	if sess, ok := sm.GetSession(nodeID); ok {
		sess.telemetryMu.Lock()
		sess.cachedGPU = t
		if t != nil {
			sess.GPUCount = t.GpuCount
		}
		sess.telemetryMu.Unlock()
	}
}

func (sm *SessionManager) UpdateProcessTelemetry(nodeID string, t *pb.ProcessTelemetry) {
	if sess, ok := sm.GetSession(nodeID); ok {
		sess.telemetryMu.Lock()
		sess.cachedProcess = t
		sess.telemetryMu.Unlock()
	}
}

func (sm *SessionManager) UpdateWorkspaceTelemetry(nodeID string, t *pb.WorkspaceTelemetry) {
	if sess, ok := sm.GetSession(nodeID); ok {
		sess.telemetryMu.Lock()
		sess.cachedWorkspace = t
		sess.telemetryMu.Unlock()
	}
}

func (sm *SessionManager) GetTelemetrySnapshot(nodeID string) (map[string]any, error) {
	sess, ok := sm.GetSession(nodeID)
	if !ok {
		return nil, fmt.Errorf("node '%s' không tồn tại trong hệ thống", nodeID)
	}

	sess.telemetryMu.RLock()
	defer sess.telemetryMu.RUnlock()

	snapshot := map[string]any{
		"node_id":        sess.NodeID,
		"status":         sess.Status,
		"last_heartbeat": sess.LastHeartbeat,
		"host":           sess.cachedHost,
		"gpu":            sess.cachedGPU,
		"processes":      sess.cachedProcess,
		"workspaces":     sess.cachedWorkspace,
	}
	return snapshot, nil
}

// DispatchCommand gửi yêu cầu lệnh typed qua kênh gRPC streaming tới Node và đợi phản hồi
func (sm *SessionManager) DispatchCommand(ctx context.Context, req *DispatchRequest) (*DispatchResponse, error) {
	if !AllowedActions[req.Action] {
		return &DispatchResponse{
			Success:      false,
			NodeID:       req.NodeID,
			Status:       "REJECTED_UNAUTHORIZED",
			ErrorCode:    "ACTION_NOT_ALLOWED",
			ErrorMessage: fmt.Sprintf("Hành động '%s' không nằm trong allowlist an toàn của LabOps", req.Action),
		}, nil
	}

	sess, ok := sm.GetSession(req.NodeID)
	if !ok || sess.Status == "OFFLINE" {
		return &DispatchResponse{
			Success:      false,
			NodeID:       req.NodeID,
			Status:       "FAILED",
			ErrorCode:    "NODE_OFFLINE",
			ErrorMessage: fmt.Sprintf("Node '%s' hiện đang ngoại tuyến hoặc chưa kết nối gRPC", req.NodeID),
		}, nil
	}

	commandID := newUUID()
	requestID := newUUID()
	respChan := make(chan *pb.CommandResponse, 1)

	sess.pendingMu.Lock()
	sess.pendingCommands[commandID] = respChan
	sess.pendingMu.Unlock()

	defer func() {
		sess.pendingMu.Lock()
		delete(sess.pendingCommands, commandID)
		sess.pendingMu.Unlock()
	}()

	cmdMsg := &pb.GatewayMessage{
		MessageId:     newUUID(),
		TimestampUnix: time.Now().Unix(),
		Payload: &pb.GatewayMessage_CommandRequest{
			CommandRequest: &pb.CommandRequest{
				CommandId:       commandID,
				RequestId:       requestID,
				NodeId:          req.NodeID,
				Action:          req.Action,
				Parameters:      req.Parameters,
				IssuedAtUnix:    time.Now().Unix(),
				DeadlineSeconds: req.TimeoutSeconds,
				IdempotencyKey:  req.IdempotencyKey,
				ActorId:         req.ActorID,
			},
		},
	}

	sess.streamMu.Lock()
	err := sess.stream.Send(cmdMsg)
	sess.streamMu.Unlock()

	if err != nil {
		return &DispatchResponse{
			Success:      false,
			CommandID:    commandID,
			NodeID:       req.NodeID,
			Status:       "FAILED",
			ErrorCode:    "STREAM_SEND_FAILED",
			ErrorMessage: fmt.Sprintf("Lỗi gửi lệnh qua stream tới node: %v", err),
		}, nil
	}

	timeout := time.Duration(req.TimeoutSeconds) * time.Second
	if timeout <= 0 {
		timeout = 15 * time.Second
	}

	select {
	case <-ctx.Done():
		return &DispatchResponse{
			Success:      false,
			CommandID:    commandID,
			NodeID:       req.NodeID,
			Status:       "REJECTED_DEADLINE_EXCEEDED",
			ErrorCode:    "CLIENT_CANCELLED",
			ErrorMessage: "Yêu cầu bị hủy từ phía client",
		}, ctx.Err()
	case <-time.After(timeout):
		return &DispatchResponse{
			Success:      false,
			CommandID:    commandID,
			NodeID:       req.NodeID,
			Status:       "REJECTED_DEADLINE_EXCEEDED",
			ErrorCode:    "DEADLINE_EXCEEDED",
			ErrorMessage: fmt.Sprintf("Quá thời gian chờ phản hồi từ node (%v)", timeout),
		}, nil
	case resp, ok := <-respChan:
		if !ok || resp == nil {
			return &DispatchResponse{
				Success:      false,
				CommandID:    commandID,
				NodeID:       req.NodeID,
				Status:       "FAILED",
				ErrorCode:    "STREAM_CLOSED",
				ErrorMessage: "Kênh truyền thông gRPC bị đóng trước khi nhận kết quả",
			}, nil
		}

		var parsedResult any
		if len(resp.ResultPayload) > 0 {
			_ = json.Unmarshal(resp.ResultPayload, &parsedResult)
		}

		return &DispatchResponse{
			Success:      resp.Status == pb.CommandResponse_SUCCESS,
			CommandID:    commandID,
			NodeID:       req.NodeID,
			Status:       resp.Status.String(),
			ErrorCode:    resp.ErrorCode,
			ErrorMessage: resp.ErrorMessage,
			Result:       parsedResult,
			DurationMs:   resp.ExecutionDurationMs,
		}, nil
	}
}

func (sm *SessionManager) RouteCommandResponse(resp *pb.CommandResponse) {
	if resp == nil {
		return
	}
	sess, ok := sm.GetSession(resp.NodeId)
	if !ok {
		return
	}

	sess.pendingMu.Lock()
	ch, exists := sess.pendingCommands[resp.CommandId]
	sess.pendingMu.Unlock()

	if exists && ch != nil {
		select {
		case ch <- resp:
		default:
		}
	}
}

func (sm *SessionManager) monitorHeartbeats() {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		sm.mu.Lock()
		now := time.Now()
		for _, sess := range sm.sessions {
			if sess.Status == "ONLINE" && now.Sub(sess.LastHeartbeat) > 20*time.Second {
				sess.Status = "DEGRADED"
				sess.DegradedReasons = append(sess.DegradedReasons, "Mất tín hiệu heartbeat quá 20 giây")
			}
			if sess.Status != "OFFLINE" && now.Sub(sess.LastHeartbeat) > 45*time.Second {
				sess.Status = "OFFLINE"
			}
		}
		sm.mu.Unlock()
	}
}
