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
	if old := sm.sessions[hello.NodeId]; old != nil {
		old.Status = "OFFLINE"
		closePendingCommands(old)
	}

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
		closePendingCommands(sess)
	}
}

func (sm *SessionManager) RemoveSessionIfCurrent(nodeID string, session *NodeSession) {
	sm.mu.Lock()
	defer sm.mu.Unlock()
	if sm.sessions[nodeID] == session {
		session.Status = "OFFLINE"
		closePendingCommands(session)
	}
}

func (sm *SessionManager) IsCurrent(nodeID string, session *NodeSession) bool {
	sm.mu.RLock()
	defer sm.mu.RUnlock()
	return sm.sessions[nodeID] == session && session.Status != "OFFLINE"
}

func closePendingCommands(session *NodeSession) {
	session.pendingMu.Lock()
	defer session.pendingMu.Unlock()
	for commandID, response := range session.pendingCommands {
		close(response)
		delete(session.pendingCommands, commandID)
	}
}

type NodeSessionSnapshot struct {
	NodeID          string
	Hostname        string
	Role            string
	AgentVersion    string
	OSInfo          string
	KernelVersion   string
	ManagementIP    string
	GPUCount        int32
	Status          string
	DegradedReasons []string
	ConnectedAt     time.Time
	LastHeartbeat   time.Time
	UptimeSeconds   int64
}

func (sm *SessionManager) GetSession(nodeID string) (*NodeSessionSnapshot, bool) {
	sm.mu.RLock()
	defer sm.mu.RUnlock()
	sess, exists := sm.sessions[nodeID]
	if !exists {
		return nil, false
	}
	return &NodeSessionSnapshot{
		NodeID: sess.NodeID, Hostname: sess.Hostname, Role: sess.Role, AgentVersion: sess.AgentVersion,
		OSInfo: sess.OSInfo, KernelVersion: sess.KernelVersion, ManagementIP: sess.ManagementIP,
		GPUCount: sess.GPUCount, Status: sess.Status, DegradedReasons: append([]string(nil), sess.DegradedReasons...),
		ConnectedAt: sess.ConnectedAt, LastHeartbeat: sess.LastHeartbeat, UptimeSeconds: sess.UptimeSeconds,
	}, true
}

func (sm *SessionManager) getSessionRef(nodeID string) (*NodeSession, bool) {
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
			DegradedReasons: append([]string(nil), sess.DegradedReasons...),
		})
	}
	return list
}

func (sm *SessionManager) UpdateHeartbeat(nodeID string, hb *pb.NodeHeartbeat) {
	sess, ok := sm.getSessionRef(nodeID)
	if ok {
		sm.UpdateHeartbeatForSession(sess, hb)
	}
}

func (sm *SessionManager) UpdateHeartbeatForSession(expected *NodeSession, hb *pb.NodeHeartbeat) {
	if hb == nil {
		return
	}
	sm.mu.Lock()
	defer sm.mu.Unlock()
	if expected == nil || sm.sessions[expected.NodeID] != expected || expected.Status == "OFFLINE" {
		return
	}
	sess := expected

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
	sess, ok := sm.getSessionRef(nodeID)
	if ok {
		sm.UpdateHostTelemetryForSession(sess, t)
	}
}

func (sm *SessionManager) UpdateHostTelemetryForSession(expected *NodeSession, t *pb.HostTelemetry) {
	sm.mu.RLock()
	current := expected != nil && sm.sessions[expected.NodeID] == expected && expected.Status != "OFFLINE"
	if current {
		expected.telemetryMu.Lock()
		expected.cachedHost = t
		expected.hostReceivedAt = time.Time{}
		if t != nil {
			expected.hostReceivedAt = time.Now().UTC()
		}
		expected.telemetryMu.Unlock()
	}
	sm.mu.RUnlock()
}

func (sm *SessionManager) UpdateGPUTelemetry(nodeID string, t *pb.GPUTelemetry) {
	sess, ok := sm.getSessionRef(nodeID)
	if ok {
		sm.UpdateGPUTelemetryForSession(sess, t)
	}
}

func (sm *SessionManager) UpdateGPUTelemetryForSession(expected *NodeSession, t *pb.GPUTelemetry) {
	sm.mu.Lock()
	current := expected != nil && sm.sessions[expected.NodeID] == expected && expected.Status != "OFFLINE"
	if current {
		if t != nil {
			expected.GPUCount = t.GpuCount
		}
		expected.telemetryMu.Lock()
		expected.cachedGPU = t
		expected.gpuReceivedAt = time.Time{}
		if t != nil {
			expected.gpuReceivedAt = time.Now().UTC()
		}
		expected.telemetryMu.Unlock()
	}
	sm.mu.Unlock()
}

func (sm *SessionManager) UpdateProcessTelemetry(nodeID string, t *pb.ProcessTelemetry) {
	sess, ok := sm.getSessionRef(nodeID)
	if ok {
		sm.UpdateProcessTelemetryForSession(sess, t)
	}
}

func (sm *SessionManager) UpdateProcessTelemetryForSession(expected *NodeSession, t *pb.ProcessTelemetry) {
	sm.mu.RLock()
	if expected != nil && sm.sessions[expected.NodeID] == expected && expected.Status != "OFFLINE" {
		expected.telemetryMu.Lock()
		expected.cachedProcess = t
		expected.telemetryMu.Unlock()
	}
	sm.mu.RUnlock()
}

func (sm *SessionManager) UpdateWorkspaceTelemetry(nodeID string, t *pb.WorkspaceTelemetry) {
	sess, ok := sm.getSessionRef(nodeID)
	if ok {
		sm.UpdateWorkspaceTelemetryForSession(sess, t)
	}
}

func (sm *SessionManager) UpdateWorkspaceTelemetryForSession(expected *NodeSession, t *pb.WorkspaceTelemetry) {
	sm.mu.RLock()
	if expected != nil && sm.sessions[expected.NodeID] == expected && expected.Status != "OFFLINE" {
		expected.telemetryMu.Lock()
		expected.cachedWorkspace = t
		expected.telemetryMu.Unlock()
	}
	sm.mu.RUnlock()
}

func (sm *SessionManager) GetTelemetrySnapshot(nodeID string) (map[string]any, error) {
	sm.mu.RLock()
	sess, ok := sm.sessions[nodeID]
	if !ok {
		sm.mu.RUnlock()
		return nil, fmt.Errorf("node '%s' không tồn tại trong hệ thống", nodeID)
	}
	status, lastHeartbeat := sess.Status, sess.LastHeartbeat
	sm.mu.RUnlock()

	sess.telemetryMu.RLock()
	defer sess.telemetryMu.RUnlock()

	snapshot := map[string]any{
		"node_id":          sess.NodeID,
		"status":           status,
		"last_heartbeat":   lastHeartbeat,
		"host":             sess.cachedHost,
		"host_received_at": telemetryTimestamp(sess.hostReceivedAt),
		"gpu":              sess.cachedGPU,
		"gpu_received_at":  telemetryTimestamp(sess.gpuReceivedAt),
		"processes":        sess.cachedProcess,
		"workspaces":       sess.cachedWorkspace,
	}
	return snapshot, nil
}

func telemetryTimestamp(receivedAt time.Time) any {
	if receivedAt.IsZero() {
		return nil
	}
	return receivedAt.UTC().Format(time.RFC3339Nano)
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

	sm.mu.RLock()
	sess, ok := sm.sessions[req.NodeID]
	if ok && sess.Status != "OFFLINE" {
		// Hold the manager read lock through Send so revocation/replacement cannot
		// mark this session offline between the check and the command write.
	} else {
		sm.mu.RUnlock()
		ok = false
	}
	if !ok {
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
	sm.mu.RUnlock()

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
	sess, ok := sm.getSessionRef(resp.NodeId)
	if ok {
		sm.RouteCommandResponseForSession(sess, resp)
	}
}

func (sm *SessionManager) RouteCommandResponseForSession(expected *NodeSession, resp *pb.CommandResponse) {
	if expected == nil || resp == nil {
		return
	}
	sm.mu.RLock()
	defer sm.mu.RUnlock()
	if sm.sessions[expected.NodeID] != expected || expected.Status == "OFFLINE" {
		return
	}
	expected.pendingMu.Lock()
	defer expected.pendingMu.Unlock()
	ch, exists := expected.pendingCommands[resp.CommandId]
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
