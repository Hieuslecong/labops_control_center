package gateway

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"time"

	pb "github.com/Hieuslecong/labops_control_center/go/proto/labops/v1"
)

// SessionManager quản lý tập trung trạng thái các kết nối gRPC của Compute Nodes
type SessionManager struct {
	mu                sync.RWMutex
	sessions          map[string]*NodeSession
	nextGeneration    map[string]uint64
	delegationSecret  string
	requireDelegation bool
	replayGuard       *ReplayGuard
}

func NewSessionManager() *SessionManager {
	sm := &SessionManager{
		sessions:       make(map[string]*NodeSession),
		nextGeneration: make(map[string]uint64),
		replayGuard:    NewReplayGuard(),
	}
	// Tự động kiểm tra node timeout mỗi 5 giây
	go sm.monitorHeartbeats()
	return sm
}

// SetDelegationSecurity cấu hình xác thực DelegationToken và ReplayGuard.
func (sm *SessionManager) SetDelegationSecurity(secret string, required bool) {
	sm.mu.Lock()
	defer sm.mu.Unlock()
	sm.delegationSecret = strings.TrimSpace(secret)
	sm.requireDelegation = required
}

func (sm *SessionManager) RegisterSession(hello *pb.NodeHello, stream pb.NodeAgentGatewayService_TunnelServer) *NodeSession {
	nodeID := strings.TrimSpace(hello.NodeId)
	sm.mu.Lock()
	gen := sm.nextGeneration[nodeID] + 1
	sm.nextGeneration[nodeID] = gen
	oldSess := sm.sessions[nodeID]

	now := time.Now()
	session := &NodeSession{
		SessionID:       newUUID(),
		Generation:      gen,
		NodeID:          nodeID,
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

	sm.sessions[nodeID] = session
	sm.mu.Unlock()

	// Đóng các pending commands của phiên cũ (nếu vừa reconnect đè phiên cũ) mà không đụng tới phiên mới
	if oldSess != nil {
		oldSess.mu.Lock()
		oldSess.superseded = true
		oldSess.Status = "SUPERSEDED"
		oldSess.mu.Unlock()

		oldSess.pendingMu.Lock()
		for cmdID, ch := range oldSess.pendingCommands {
			close(ch)
			delete(oldSess.pendingCommands, cmdID)
		}
		oldSess.pendingMu.Unlock()
	}

	return session
}

// RemoveSessionIfMatch chỉ đánh dấu OFFLINE nếu SessionID đang active trùng với session đã ngắt kết nối.
func (sm *SessionManager) RemoveSessionIfMatch(nodeID, sessionID string) bool {
	sm.mu.Lock()
	sess, exists := sm.sessions[nodeID]
	if !exists || sess == nil || sess.SessionID != sessionID {
		sm.mu.Unlock()
		return false
	}
	sm.mu.Unlock()

	sess.mu.Lock()
	sess.Status = "OFFLINE"
	sess.mu.Unlock()

	sess.pendingMu.Lock()
	for cmdID, ch := range sess.pendingCommands {
		close(ch)
		delete(sess.pendingCommands, cmdID)
	}
	sess.pendingMu.Unlock()
	return true
}

func (sm *SessionManager) RemoveSession(nodeID string) {
	sm.mu.RLock()
	sess, exists := sm.sessions[nodeID]
	sm.mu.RUnlock()
	if !exists || sess == nil {
		return
	}

	sess.mu.Lock()
	sess.Status = "OFFLINE"
	sess.mu.Unlock()

	sess.pendingMu.Lock()
	for cmdID, ch := range sess.pendingCommands {
		close(ch)
		delete(sess.pendingCommands, cmdID)
	}
	sess.pendingMu.Unlock()
}

func (sm *SessionManager) getLiveSession(nodeID string) (*NodeSession, bool) {
	sm.mu.RLock()
	defer sm.mu.RUnlock()
	sess, exists := sm.sessions[nodeID]
	return sess, exists
}

// GetSession trả về bản sao snapshot an toàn (race-free) của phiên kết nối node.
func (sm *SessionManager) GetSession(nodeID string) (*NodeSession, bool) {
	sess, exists := sm.getLiveSession(nodeID)
	if !exists || sess == nil {
		return nil, false
	}
	sess.mu.RLock()
	reasonsCopy := append([]string(nil), sess.DegradedReasons...)
	snap := &NodeSession{
		SessionID:       sess.SessionID,
		Generation:      sess.Generation,
		NodeID:          sess.NodeID,
		Hostname:        sess.Hostname,
		Role:            sess.Role,
		AgentVersion:    sess.AgentVersion,
		OSInfo:          sess.OSInfo,
		KernelVersion:   sess.KernelVersion,
		ManagementIP:    sess.ManagementIP,
		GPUCount:        sess.GPUCount,
		Status:          sess.Status,
		DegradedReasons: reasonsCopy,
		ConnectedAt:     sess.ConnectedAt,
		LastHeartbeat:   sess.LastHeartbeat,
		UptimeSeconds:   sess.UptimeSeconds,
	}
	sess.mu.RUnlock()
	return snap, true
}

func (sm *SessionManager) ListNodes() []*NodeSummary {
	sm.mu.RLock()
	liveList := make([]*NodeSession, 0, len(sm.sessions))
	for _, sess := range sm.sessions {
		liveList = append(liveList, sess)
	}
	sm.mu.RUnlock()

	list := make([]*NodeSummary, 0, len(liveList))
	for _, sess := range liveList {
		sess.mu.RLock()
		reasonsCopy := append([]string(nil), sess.DegradedReasons...)
		list = append(list, &NodeSummary{
			NodeID:          sess.NodeID,
			Hostname:        sess.Hostname,
			Role:            sess.Role,
			Status:          sess.Status,
			AgentVersion:    sess.AgentVersion,
			ManagementIP:    sess.ManagementIP,
			GPUCount:        sess.GPUCount,
			SessionID:       sess.SessionID,
			Generation:      sess.Generation,
			ConnectedAt:     sess.ConnectedAt,
			LastHeartbeat:   sess.LastHeartbeat,
			DegradedReasons: reasonsCopy,
		})
		sess.mu.RUnlock()
	}
	return list
}

func (sm *SessionManager) UpdateHeartbeat(nodeID string, hb *pb.NodeHeartbeat) {
	sess, exists := sm.getLiveSession(nodeID)
	if !exists || sess == nil || hb == nil {
		return
	}

	sess.mu.Lock()
	defer sess.mu.Unlock()
	if sess.superseded {
		return
	}

	sess.LastHeartbeat = time.Now()
	sess.UptimeSeconds = hb.UptimeSeconds

	if hb.Health != nil {
		switch hb.Health.State {
		case pb.NodeHealthStatus_HEALTHY:
			sess.Status = "ONLINE"
			sess.DegradedReasons = nil
		case pb.NodeHealthStatus_DEGRADED:
			sess.Status = "DEGRADED"
			sess.DegradedReasons = append([]string(nil), hb.Health.DegradedReasons...)
		case pb.NodeHealthStatus_UNHEALTHY:
			sess.Status = "UNHEALTHY"
			sess.DegradedReasons = append([]string(nil), hb.Health.DegradedReasons...)
		}
	} else {
		sess.Status = "ONLINE"
	}
}

func (sm *SessionManager) UpdateHostTelemetry(nodeID string, t *pb.HostTelemetry) {
	if sess, ok := sm.getLiveSession(nodeID); ok {
		sess.telemetryMu.Lock()
		sess.cachedHost = t
		sess.telemetryMu.Unlock()
	}
}

func (sm *SessionManager) UpdateGPUTelemetry(nodeID string, t *pb.GPUTelemetry) {
	if sess, ok := sm.getLiveSession(nodeID); ok {
		sess.telemetryMu.Lock()
		sess.cachedGPU = t
		sess.telemetryMu.Unlock()
		if t != nil {
			sess.mu.Lock()
			sess.GPUCount = t.GpuCount
			sess.mu.Unlock()
		}
	}
}

func (sm *SessionManager) UpdateProcessTelemetry(nodeID string, t *pb.ProcessTelemetry) {
	if sess, ok := sm.getLiveSession(nodeID); ok {
		sess.telemetryMu.Lock()
		sess.cachedProcess = t
		sess.telemetryMu.Unlock()
	}
}

func (sm *SessionManager) UpdateWorkspaceTelemetry(nodeID string, t *pb.WorkspaceTelemetry) {
	if sess, ok := sm.getLiveSession(nodeID); ok {
		sess.telemetryMu.Lock()
		sess.cachedWorkspace = t
		sess.telemetryMu.Unlock()
	}
}

func (sm *SessionManager) GetTelemetrySnapshot(nodeID string) (map[string]any, error) {
	sess, ok := sm.getLiveSession(nodeID)
	if !ok {
		return nil, fmt.Errorf("node '%s' không tồn tại trong hệ thống", nodeID)
	}

	sess.mu.RLock()
	status := sess.Status
	lastHB := sess.LastHeartbeat
	sessionID := sess.SessionID
	gen := sess.Generation
	sess.mu.RUnlock()

	sess.telemetryMu.RLock()
	defer sess.telemetryMu.RUnlock()

	snapshot := map[string]any{
		"node_id":        sess.NodeID,
		"session_id":     sessionID,
		"generation":     gen,
		"status":         status,
		"last_heartbeat": lastHB,
		"host":           sess.cachedHost,
		"gpu":            sess.cachedGPU,
		"processes":      sess.cachedProcess,
		"workspaces":     sess.cachedWorkspace,
	}
	return snapshot, nil
}

// DispatchCommand gửi yêu cầu lệnh typed qua kênh gRPC streaming tới Node và đợi phản hồi.
func (sm *SessionManager) DispatchCommand(ctx context.Context, req *DispatchRequest) (*DispatchResponse, error) {
	if req == nil || strings.TrimSpace(req.NodeID) == "" || strings.TrimSpace(req.Action) == "" {
		return &DispatchResponse{
			Success:      false,
			Status:       "REJECTED_INVALID_PARAMS",
			ErrorCode:    "INVALID_DISPATCH_REQUEST",
			ErrorMessage: "Thiếu node_id hoặc action",
		}, nil
	}

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
	delSecret := sm.delegationSecret
	reqDelegation := sm.requireDelegation
	sm.mu.RUnlock()

	actorScope := strings.TrimSpace(req.ActorID)
	paramsHash := ComputeParamsHash(req.Parameters)
	requestID := strings.TrimSpace(req.RequestID)
	if requestID == "" {
		requestID = newUUID()
	}

	if reqDelegation || strings.TrimSpace(req.AuthorizationContext) != "" {
		claims, err := VerifyDelegationToken(delSecret, req.AuthorizationContext, time.Now())
		if err != nil {
			return &DispatchResponse{
				Success:      false,
				NodeID:       req.NodeID,
				Status:       "REJECTED_UNAUTHORIZED",
				ErrorCode:    "DELEGATION_INVALID",
				ErrorMessage: err.Error(),
			}, nil
		}
		if err := ActionAuthorizationCheck(claims, req.NodeID, req.Action, req.Parameters); err != nil {
			return &DispatchResponse{
				Success:      false,
				NodeID:       req.NodeID,
				Status:       "REJECTED_UNAUTHORIZED",
				ErrorCode:    "ACTION_UNAUTHORIZED",
				ErrorMessage: err.Error(),
			}, nil
		}
		if err := sm.replayGuard.ReserveNonce(claims.Nonce, time.Unix(claims.ExpiresAt, 0)); err != nil {
			return &DispatchResponse{
				Success:      false,
				NodeID:       req.NodeID,
				Status:       "REJECTED_UNAUTHORIZED",
				ErrorCode:    "REPLAY_DETECTED",
				ErrorMessage: err.Error(),
			}, nil
		}
		actorScope = claims.ActorID
		if claims.RequestID != "" {
			requestID = claims.RequestID
		}
	}

	if req.IdempotencyKey != "" {
		if cached, hit, idempErr := sm.replayGuard.CheckIdempotency(actorScope, req.Action, req.IdempotencyKey, paramsHash); idempErr != nil {
			return &DispatchResponse{
				Success:      false,
				NodeID:       req.NodeID,
				Status:       "REJECTED_INVALID_PARAMS",
				ErrorCode:    "IDEMPOTENCY_CONFLICT",
				ErrorMessage: idempErr.Error(),
			}, nil
		} else if hit && cached != nil {
			return cached, nil
		}
	}

	sess, ok := sm.getLiveSession(req.NodeID)
	if !ok || sess == nil {
		return &DispatchResponse{
			Success:      false,
			NodeID:       req.NodeID,
			Status:       "FAILED",
			ErrorCode:    "NODE_OFFLINE",
			ErrorMessage: fmt.Sprintf("Node '%s' hiện đang ngoại tuyến hoặc chưa kết nối gRPC", req.NodeID),
		}, nil
	}

	sess.mu.RLock()
	sessStatus := sess.Status
	superseded := sess.superseded
	sess.mu.RUnlock()

	if superseded || sessStatus == "OFFLINE" || sessStatus == "SUPERSEDED" {
		return &DispatchResponse{
			Success:      false,
			NodeID:       req.NodeID,
			Status:       "FAILED",
			ErrorCode:    "NODE_OFFLINE",
			ErrorMessage: fmt.Sprintf("Node '%s' hiện đang ngoại tuyến hoặc phiên đã hết hạn", req.NodeID),
		}, nil
	}

	commandID := newUUID()
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
				CommandId:            commandID,
				RequestId:            requestID,
				NodeId:               req.NodeID,
				Action:               req.Action,
				Parameters:           req.Parameters,
				IssuedAtUnix:         time.Now().Unix(),
				DeadlineSeconds:      req.TimeoutSeconds,
				IdempotencyKey:       req.IdempotencyKey,
				ActorId:              actorScope,
				AuthorizationContext: req.AuthorizationContext,
			},
		},
	}

	sess.streamMu.Lock()
	var sendErr error
	if sess.stream == nil {
		sendErr = fmt.Errorf("node stream is not active")
	} else {
		sendErr = sess.stream.Send(cmdMsg)
	}
	sess.streamMu.Unlock()

	if sendErr != nil {
		return &DispatchResponse{
			Success:      false,
			CommandID:    commandID,
			NodeID:       req.NodeID,
			Status:       "FAILED",
			ErrorCode:    "STREAM_SEND_FAILED",
			ErrorMessage: fmt.Sprintf("Lỗi gửi lệnh qua stream tới node: %v", sendErr),
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

		out := &DispatchResponse{
			Success:      resp.Status == pb.CommandResponse_SUCCESS,
			CommandID:    commandID,
			NodeID:       req.NodeID,
			Status:       resp.Status.String(),
			ErrorCode:    resp.ErrorCode,
			ErrorMessage: resp.ErrorMessage,
			Result:       parsedResult,
			DurationMs:   resp.ExecutionDurationMs,
		}
		if req.IdempotencyKey != "" {
			sm.replayGuard.RecordIdempotency(actorScope, req.Action, req.IdempotencyKey, paramsHash, out, 10*time.Minute)
		}
		return out, nil
	}
}

// RouteCommandResponse ràng buộc chặt chẽ NodeID của luồng gRPC đã xác thực mTLS với payload phản hồi.
func (sm *SessionManager) RouteCommandResponse(authenticatedNodeID string, resp *pb.CommandResponse) {
	if resp == nil || strings.TrimSpace(authenticatedNodeID) == "" {
		return
	}
	if resp.NodeId != "" && !strings.EqualFold(resp.NodeId, authenticatedNodeID) {
		// Chặn spoof CommandResponse sang phiên của node khác
		return
	}
	sess, ok := sm.getLiveSession(authenticatedNodeID)
	if !ok || sess == nil {
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
		sm.mu.RLock()
		snapshot := make([]*NodeSession, 0, len(sm.sessions))
		for _, sess := range sm.sessions {
			snapshot = append(snapshot, sess)
		}
		sm.mu.RUnlock()

		now := time.Now()
		for _, sess := range snapshot {
			sess.mu.Lock()
			if !sess.superseded {
				if sess.Status == "ONLINE" && now.Sub(sess.LastHeartbeat) > 20*time.Second {
					sess.Status = "DEGRADED"
					sess.DegradedReasons = append(sess.DegradedReasons, "Mất tín hiệu heartbeat quá 20 giây")
				}
				if sess.Status != "OFFLINE" && now.Sub(sess.LastHeartbeat) > 45*time.Second {
					sess.Status = "OFFLINE"
				}
			}
			sess.mu.Unlock()
		}
	}
}
