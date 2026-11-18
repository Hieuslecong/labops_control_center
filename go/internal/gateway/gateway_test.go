package gateway

import (
	"bytes"
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"github.com/Hieuslecong/labops_control_center/go/internal/pki"
	pb "github.com/Hieuslecong/labops_control_center/go/proto/labops/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
)

func TestSessionManagerLifecycleAndDispatch(t *testing.T) {
	sm := NewSessionManager()

	hello := &pb.NodeHello{
		NodeId:       "track",
		Hostname:     "aiotlab-rack",
		Role:         "HYBRID",
		AgentVersion: "2.0.0",
		ManagementIp: "192.168.167.250",
		GpuCount:     3,
	}

	// 1. Đăng ký session
	sess := sm.RegisterSession(hello, nil)
	if sess == nil || sess.Status != "ONLINE" {
		t.Fatalf("Session đăng ký không đúng trạng thái: %v", sess)
	}

	nodes := sm.ListNodes()
	if len(nodes) != 1 || nodes[0].NodeID != "track" {
		t.Fatalf("ListNodes không trả về đúng danh sách: %v", nodes)
	}

	// 2. Thử nghiệm cập nhật Heartbeat
	hb := &pb.NodeHeartbeat{
		NodeId:         "track",
		SequenceNumber: 1,
		UptimeSeconds:  120,
		Health: &pb.NodeHealthStatus{
			State:           pb.NodeHealthStatus_DEGRADED,
			DegradedReasons: []string{"Nhiệt độ GPU cao"},
		},
	}
	sm.UpdateHeartbeat("track", hb)
	sess, _ = sm.GetSession("track")
	if sess.Status != "DEGRADED" || len(sess.DegradedReasons) == 0 {
		t.Fatalf("Heartbeat không cập nhật trạng thái DEGRADED: %v", sess)
	}

	// 3. Thử nghiệm DispatchCommand khi hành động không thuộc allowlist
	resp, err := sm.DispatchCommand(context.Background(), &DispatchRequest{
		NodeID: "track",
		Action: "RM_RF_SLASH_DANGEROUS",
	})
	if err != nil {
		t.Fatalf("Lỗi bất ngờ: %v", err)
	}
	if resp.Success || resp.Status != "REJECTED_UNAUTHORIZED" {
		t.Fatalf("Kỳ vọng từ chối lệnh cấm nhưng lại trả về: %v", resp)
	}
}

func TestHTTPControlPlaneAPI(t *testing.T) {
	tempDir, err := os.MkdirTemp("", "labops-gw-http-test-*")
	if err != nil {
		t.Fatalf("Lỗi tạo tempdir: %v", err)
	}
	defer os.RemoveAll(tempDir)

	ca, err := pki.GenerateOrLoadCA(tempDir)
	if err != nil {
		t.Fatalf("Lỗi CA: %v", err)
	}

	sm := NewSessionManager()
	sm.RegisterSession(&pb.NodeHello{
		NodeId:   "workstation",
		Hostname: "aiot-ws1",
		GpuCount: 1,
	}, nil)

	tm := pki.NewEnrollmentTokenManager("test-salt")
	rm := pki.NewRevocationManager()

	controlSecret := "test-secret-salt-2026"
	httpSrv := NewHTTPControlPlaneServer("127.0.0.1", 0, sm, ca, tm, rm, controlSecret, nil)

	// 1. Test /health (Public, no auth needed)
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	httpSrv.handleHealth(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("Health check lỗi: %d", w.Code)
	}

	// 2. Test /api/v1/gateway/nodes không có token xác thực -> Phải bị từ chối 401 Unauthorized
	reqUnauth := httptest.NewRequest(http.MethodGet, "/api/v1/gateway/nodes", nil)
	wUnauth := httptest.NewRecorder()
	httpSrv.handleNodes(wUnauth, reqUnauth)
	if wUnauth.Code != http.StatusUnauthorized {
		t.Fatalf("Kỳ vọng 401 khi chưa xác thực nhưng nhận được: %d", wUnauth.Code)
	}

	// 2b. Test /api/v1/gateway/nodes có Bearer token hợp lệ -> 200 OK
	req = httptest.NewRequest(http.MethodGet, "/api/v1/gateway/nodes", nil)
	req.Header.Set("Authorization", "Bearer "+controlSecret)
	w = httptest.NewRecorder()
	httpSrv.handleNodes(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("Nodes list lỗi: %d", w.Code)
	}
	var res map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &res)
	if total, ok := res["total_count"].(float64); !ok || int(total) != 1 {
		t.Fatalf("Total count không chính xác: %v", res)
	}

	// 3. Test /api/v1/gateway/enroll có xác thực
	enrollBody, _ := json.Marshal(map[string]any{
		"node_id":     "ws-node-new",
		"ttl_minutes": 10,
	})
	req = httptest.NewRequest(http.MethodPost, "/api/v1/gateway/enroll", bytes.NewReader(enrollBody))
	req.Header.Set("Authorization", "Bearer "+controlSecret)
	w = httptest.NewRecorder()
	httpSrv.handleEnrollToken(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("Enroll token lỗi: %d", w.Code)
	}
	var enrollRes map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &enrollRes)
	token, _ := enrollRes["token"].(string)
	if token == "" {
		t.Fatal("Token rỗng")
	}

	// 3b. Test AT-04: Thử dùng token của ws-node-new để cấp cert cho node khác (attacker-node) -> Phải bị từ chối 401
	issueAttackBody, _ := json.Marshal(map[string]any{
		"node_id":          "attacker-node",
		"enrollment_token": token,
	})
	reqAttack := httptest.NewRequest(http.MethodPost, "/api/v1/gateway/issue-cert", bytes.NewReader(issueAttackBody))
	wAttack := httptest.NewRecorder()
	httpSrv.handleIssueCert(wAttack, reqAttack)
	if wAttack.Code != http.StatusUnauthorized {
		t.Fatalf("AT-04 Vi phạm: Dùng token node này cấp cho node khác phải bị từ chối 401, nhận: %d", wAttack.Code)
	}

	// 4. Test /api/v1/gateway/issue-cert với token đúng node_id
	issueBody, _ := json.Marshal(map[string]any{
		"node_id":          "ws-node-new",
		"enrollment_token": token,
	})
	req = httptest.NewRequest(http.MethodPost, "/api/v1/gateway/issue-cert", bytes.NewReader(issueBody))
	w = httptest.NewRecorder()
	httpSrv.handleIssueCert(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("Issue cert lỗi: %d, body: %s", w.Code, w.Body.String())
	}
	var certRes map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &certRes)
	if certRes["certificate"] == nil || certRes["private_key"] == nil {
		t.Fatal("Kết quả cấp chứng chỉ thiếu cert hoặc key")
	}

	// 5. Test replay issue-cert với cùng token (phải bị từ chối 401)
	reqReplay := httptest.NewRequest(http.MethodPost, "/api/v1/gateway/issue-cert", bytes.NewReader(issueBody))
	wReplay := httptest.NewRecorder()
	httpSrv.handleIssueCert(wReplay, reqReplay)
	if wReplay.Code != http.StatusUnauthorized {
		t.Fatalf("Kỳ vọng 401 khi dùng lại token nhưng nhận được: %d", wReplay.Code)
	}
}

func TestGRPCGatewayMTLSTunnel(t *testing.T) {
	tempDir, err := os.MkdirTemp("", "labops-gw-grpc-test-*")
	if err != nil {
		t.Fatalf("Lỗi tạo tempdir: %v", err)
	}
	defer os.RemoveAll(tempDir)

	ca, err := pki.GenerateOrLoadCA(tempDir)
	if err != nil {
		t.Fatalf("Lỗi tạo CA: %v", err)
	}

	// Cấp cert cho server và client
	gwCert, err := pki.IssueGatewayCert(ca, []string{"127.0.0.1", "localhost"})
	if err != nil {
		t.Fatalf("Lỗi tạo cert gateway: %v", err)
	}

	nodeID := "track"
	nodeCert, err := pki.IssueNodeCert(ca, nodeID)
	if err != nil {
		t.Fatalf("Lỗi tạo cert node: %v", err)
	}

	rm := pki.NewRevocationManager()
	rm.AllowNode(nodeID)

	serverTLS, err := pki.BuildServerTLSConfig(ca.CertPEM, gwCert.CertPEM, gwCert.KeyPEM, rm)
	if err != nil {
		t.Fatalf("Lỗi server TLS config: %v", err)
	}

	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("Lỗi listen: %v", err)
	}
	defer lis.Close()

	gwConfig := &Config{
		BindAddr:          "127.0.0.1",
		GRPCPort:          lis.Addr().(*net.TCPAddr).Port,
		RevocationManager: rm,
	}

	srv, err := NewServer(gwConfig, ca, serverTLS)
	if err != nil {
		t.Fatalf("Lỗi NewServer: %v", err)
	}

	go func() {
		_ = srv.grpcServer.Serve(lis)
	}()
	defer srv.Stop()

	// Client mTLS config
	clientTLS, err := pki.BuildClientTLSConfig(ca.CertPEM, nodeCert.CertPEM, nodeCert.KeyPEM, "localhost")
	if err != nil {
		t.Fatalf("Lỗi client TLS config: %v", err)
	}

	conn, err := grpc.Dial(lis.Addr().String(), grpc.WithTransportCredentials(credentials.NewTLS(clientTLS)))
	if err != nil {
		t.Fatalf("Lỗi grpc.Dial: %v", err)
	}
	defer conn.Close()

	client := pb.NewNodeAgentGatewayServiceClient(conn)
	stream, err := client.Tunnel(context.Background())
	if err != nil {
		t.Fatalf("Lỗi mở stream Tunnel: %v", err)
	}

	// Gửi NodeHello
	helloMsg := &pb.AgentMessage{
		MessageId:     "msg-1",
		TimestampUnix: time.Now().Unix(),
		NodeId:        nodeID,
		Payload: &pb.AgentMessage_Hello{
			Hello: &pb.NodeHello{
				NodeId:       nodeID,
				Hostname:     "aiotlab-rack",
				Role:         "HYBRID",
				AgentVersion: "2.0.0",
				GpuCount:     3,
			},
		},
	}

	if err := stream.Send(helloMsg); err != nil {
		t.Fatalf("Lỗi gửi NodeHello: %v", err)
	}

	// Đợi RegistrationAck từ Gateway
	respMsg, err := stream.Recv()
	if err != nil {
		t.Fatalf("Lỗi nhận ack từ Gateway: %v", err)
	}

	regAck := respMsg.GetRegistrationAck()
	if regAck == nil || !regAck.Approved {
		t.Fatalf("RegistrationAck không hợp lệ hoặc không được duyệt: %v", respMsg)
	}

	// Gửi Heartbeat
	hbMsg := &pb.AgentMessage{
		MessageId:     "msg-2",
		TimestampUnix: time.Now().Unix(),
		NodeId:        nodeID,
		Payload: &pb.AgentMessage_Heartbeat{
			Heartbeat: &pb.NodeHeartbeat{
				NodeId:         nodeID,
				SequenceNumber: 1,
				UptimeSeconds:  50,
				Health: &pb.NodeHealthStatus{
					State: pb.NodeHealthStatus_HEALTHY,
				},
			},
		},
	}
	if err := stream.Send(hbMsg); err != nil {
		t.Fatalf("Lỗi gửi heartbeat: %v", err)
	}

	// Nhận HeartbeatAck
	ackMsg, err := stream.Recv()
	if err != nil {
		t.Fatalf("Lỗi nhận heartbeat ack: %v", err)
	}
	if ackMsg.GetHeartbeatAck() == nil {
		t.Fatalf("Kỳ vọng HeartbeatAck nhưng nhận được: %v", ackMsg)
	}

	// Kiểm tra session trên server đã cập nhật
	sess, ok := srv.SessionManager().GetSession(nodeID)
	if !ok || sess.Status != "ONLINE" {
		t.Fatalf("Session chưa được cập nhật ONLINE: %v", sess)
	}
}

func TestSessionReconnectRaceDoesNotMarkNewSessionOffline(t *testing.T) {
	sm := NewSessionManager()
	sess1 := sm.RegisterSession(&pb.NodeHello{NodeId: "workstation", Hostname: "aiot-ws1"}, nil)
	sess2 := sm.RegisterSession(&pb.NodeHello{NodeId: "workstation", Hostname: "aiot-ws1"}, nil)

	if sess2.Generation <= sess1.Generation || sess2.SessionID == sess1.SessionID {
		t.Fatalf("Kỳ vọng generation & session ID mới khi reconnect: s1=(%d,%s), s2=(%d,%s)",
			sess1.Generation, sess1.SessionID, sess2.Generation, sess2.SessionID)
	}

	// Stream cũ đóng muộn gọi RemoveSessionIfMatch -> KHÔNG ĐƯỢC đánh dấu sess2 OFFLINE!
	if removed := sm.RemoveSessionIfMatch("workstation", sess1.SessionID); removed {
		t.Fatal("RemoveSessionIfMatch không được xóa/đánh dấu OFFLINE phiên mới hơn")
	}
	curr, ok := sm.GetSession("workstation")
	if !ok || curr.Status != "ONLINE" || curr.SessionID != sess2.SessionID {
		t.Fatalf("Phiên hiện tại bị đánh dấu OFFLINE nhầm khi stream cũ ngắt: %+v", curr)
	}

	// Chỉ khi sess2 đóng mới đánh dấu OFFLINE
	if removed := sm.RemoveSessionIfMatch("workstation", sess2.SessionID); !removed {
		t.Fatal("Kỳ vọng RemoveSessionIfMatch trả true khi khớp sess2.SessionID")
	}
	currAfter, _ := sm.GetSession("workstation")
	if currAfter.Status != "OFFLINE" {
		t.Fatalf("Kỳ vọng OFFLINE sau khi sess2 ngắt, nhận: %s", currAfter.Status)
	}
}

func TestGatewayDelegationAndReplayProtection(t *testing.T) {
	secret := "gateway-delegation-hmac-secret-2026"
	sm := NewSessionManager()
	sm.SetDelegationSecurity(secret, true)
	sm.RegisterSession(&pb.NodeHello{NodeId: "workstation", Hostname: "aiot-ws1"}, nil)

	// 1. Dispatch không có DelegationToken -> bị từ chối REJECTED_UNAUTHORIZED
	respNoTok, err := sm.DispatchCommand(context.Background(), &DispatchRequest{
		NodeID: "workstation",
		Action: "INSPECT_HOST",
	})
	if err != nil {
		t.Fatal(err)
	}
	if respNoTok.Status != "REJECTED_UNAUTHORIZED" || respNoTok.ErrorCode != "DELEGATION_INVALID" {
		t.Fatalf("Kỳ vọng từ chối thiếu token, nhận: %+v", respNoTok)
	}

	// 2. RESEARCHER cố gắng gọi action Admin (PROVISION_DOCKER_WORKSPACE / KILL_PROCESS) -> REJECTED
	paramsAdmin := map[string]string{"workspace_id": "ws-target", "owner": "other"}
	resTok, err := SignDelegationToken(secret, DelegationClaims{
		ActorID:      "researcher1",
		ActorRole:    "RESEARCHER",
		ServiceID:    "fastapi-bff",
		Action:       "PROVISION_DOCKER_WORKSPACE",
		NodeID:       "workstation",
		WorkspaceID:  "ws-researcher1",
		Audience:     DelegationAudience,
		RequestID:    "req-1",
		Nonce:        "nonce-1",
		ParamsSHA256: ComputeParamsHash(paramsAdmin),
		IssuedAt:     time.Now().Unix(),
		ExpiresAt:    time.Now().Add(time.Minute).Unix(),
	})
	if err != nil {
		t.Fatal(err)
	}
	respPriv, _ := sm.DispatchCommand(context.Background(), &DispatchRequest{
		NodeID:               "workstation",
		Action:               "PROVISION_DOCKER_WORKSPACE",
		Parameters:           paramsAdmin,
		ActorID:              "researcher1",
		AuthorizationContext: resTok,
	})
	if respPriv.Status != "REJECTED_UNAUTHORIZED" || respPriv.ErrorCode != "ACTION_UNAUTHORIZED" {
		t.Fatalf("RESEARCHER không được phép chạy action ADMIN: %+v", respPriv)
	}

	// 3. RESEARCHER tác động workspace người khác (STOP_DOCKER_WORKSPACE) -> REJECTED
	crossTok, _ := SignDelegationToken(secret, DelegationClaims{
		ActorID:      "researcher1",
		ActorRole:    "RESEARCHER",
		ServiceID:    "fastapi-bff",
		Action:       "STOP_DOCKER_WORKSPACE",
		NodeID:       "workstation",
		WorkspaceID:  "ws-researcher1",
		Audience:     DelegationAudience,
		RequestID:    "req-2",
		Nonce:        "nonce-2",
		ParamsSHA256: ComputeParamsHash(paramsAdmin),
		IssuedAt:     time.Now().Unix(),
		ExpiresAt:    time.Now().Add(time.Minute).Unix(),
	})
	respCross, _ := sm.DispatchCommand(context.Background(), &DispatchRequest{
		NodeID:               "workstation",
		Action:               "STOP_DOCKER_WORKSPACE",
		Parameters:           paramsAdmin,
		AuthorizationContext: crossTok,
	})
	if respCross.Status != "REJECTED_UNAUTHORIZED" {
		t.Fatalf("RESEARCHER không được phép dừng workspace người khác: %+v", respCross)
	}

	// 4. Replay cùng Nonce với token ADMIN hợp lệ -> lần 2 bị chặn REPLAY_DETECTED
	ownParams := map[string]string{"workspace_id": "ws-researcher1", "owner": "researcher1", "container_id": "cid-1"}
	validAdminTok, _ := SignDelegationToken(secret, DelegationClaims{
		ActorID:      "admin1",
		ActorRole:    "ADMIN",
		ServiceID:    "fastapi-bff",
		Action:       "STOP_DOCKER_WORKSPACE",
		NodeID:       "workstation",
		WorkspaceID:  "ws-researcher1",
		Audience:     DelegationAudience,
		RequestID:    "req-3",
		Nonce:        "nonce-unique-3",
		ParamsSHA256: ComputeParamsHash(ownParams),
		IssuedAt:     time.Now().Unix(),
		ExpiresAt:    time.Now().Add(time.Minute).Unix(),
	})
	// stream == nil nên lần 1 qua tới STREAM_SEND_FAILED sau khi Pass auth + ReserveNonce
	r1, _ := sm.DispatchCommand(context.Background(), &DispatchRequest{
		NodeID:               "workstation",
		Action:               "STOP_DOCKER_WORKSPACE",
		Parameters:           ownParams,
		AuthorizationContext: validAdminTok,
	})
	if r1.ErrorCode != "STREAM_SEND_FAILED" {
		t.Fatalf("Kỳ vọng auth hợp lệ đi qua ReplayGuard tới bước send stream, nhận: %+v", r1)
	}
	r2, _ := sm.DispatchCommand(context.Background(), &DispatchRequest{
		NodeID:               "workstation",
		Action:               "STOP_DOCKER_WORKSPACE",
		Parameters:           ownParams,
		AuthorizationContext: validAdminTok,
	})
	if r2.Status != "REJECTED_UNAUTHORIZED" || r2.ErrorCode != "REPLAY_DETECTED" {
		t.Fatalf("Kỳ vọng REPLAY_DETECTED khi tái sử dụng nonce, nhận: %+v", r2)
	}
}

func TestRouteCommandResponse_RejectsCrossNodeSpoof(t *testing.T) {
	sm := NewSessionManager()
	sm.RegisterSession(&pb.NodeHello{NodeId: "node-victim", Hostname: "victim"}, nil)
	liveVictim, _ := sm.getLiveSession("node-victim")
	ch := make(chan *pb.CommandResponse, 1)
	liveVictim.pendingMu.Lock()
	liveVictim.pendingCommands["cmd-123"] = ch
	liveVictim.pendingMu.Unlock()

	// Attacker stream cố gửi response mang NodeId = node-victim -> bị chặn!
	sm.RouteCommandResponse("node-attacker", &pb.CommandResponse{
		CommandId: "cmd-123",
		NodeId:    "node-victim",
		Status:    pb.CommandResponse_SUCCESS,
	})
	select {
	case <-ch:
		t.Fatal("Không được phép định tuyến CommandResponse sang node khác qua node-attacker stream")
	default:
	}

	// Stream hợp lệ của node-victim mới định tuyến được
	sm.RouteCommandResponse("node-victim", &pb.CommandResponse{
		CommandId: "cmd-123",
		NodeId:    "node-victim",
		Status:    pb.CommandResponse_SUCCESS,
	})
	select {
	case resp := <-ch:
		if resp.Status != pb.CommandResponse_SUCCESS {
			t.Fatalf("Expected SUCCESS, got %v", resp.Status)
		}
	default:
		t.Fatal("Expected legitimate response to route to node-victim")
	}
}

func TestHTTPControlPlane_NoAutoUnrevoke(t *testing.T) {
	tmpDir := t.TempDir()
	ca, err := pki.GenerateOrLoadCA(tmpDir)
	if err != nil {
		t.Fatal(err)
	}
	sm := NewSessionManager()
	tm := pki.NewEnrollmentTokenManager("test-enroll-secret-2026")
	rm := pki.NewRevocationManagerWithPersistence(tmpDir + "/rev.json")
	if err := rm.AllowNode("node-rev"); err != nil {
		t.Fatal(err)
	}
	if err := rm.RevokeNode("node-rev", "compromised"); err != nil {
		t.Fatal(err)
	}
	controlSecret := "test-control-secret-2026"
	httpSrv := NewHTTPControlPlaneServer("127.0.0.1", 0, sm, ca, tm, rm, controlSecret, nil)

	body, _ := json.Marshal(map[string]any{"node_id": "node-rev", "ttl_minutes": 5})
	req := httptest.NewRequest(http.MethodPost, "/api/v1/gateway/enroll", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+controlSecret)
	w := httptest.NewRecorder()
	httpSrv.handleEnrollToken(w, req)
	if w.Code != http.StatusForbidden {
		t.Fatalf("Kỳ vọng 403 Forbidden khi cố enroll lại node đã bị thu hồi, nhận: %d", w.Code)
	}
}
