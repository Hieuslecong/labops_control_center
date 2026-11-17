package gateway

import (
	"bytes"
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
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
	snapshot, _ := sm.GetSession("track")
	if snapshot.Status != "DEGRADED" || len(snapshot.DegradedReasons) == 0 {
		t.Fatalf("Heartbeat không cập nhật trạng thái DEGRADED: %v", snapshot)
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

func TestTelemetrySnapshotReportsPerStreamGatewayReceiptTimes(t *testing.T) {
	sm := NewSessionManager()
	sess := sm.RegisterSession(&pb.NodeHello{NodeId: "track"}, nil)
	started := time.Now().UTC().Add(-time.Second)
	sm.UpdateHostTelemetryForSession(sess, &pb.HostTelemetry{NodeId: "track", Status: "online"})
	sm.UpdateGPUTelemetryForSession(sess, &pb.GPUTelemetry{NodeId: "track", Status: "VERIFIED"})

	snapshot, err := sm.GetTelemetrySnapshot("track")
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"host_received_at", "gpu_received_at"} {
		raw, ok := snapshot[key].(string)
		if !ok || raw == "" {
			t.Fatalf("snapshot has no gateway receipt time for %s: %#v", key, snapshot[key])
		}
		observed, err := time.Parse(time.RFC3339Nano, raw)
		if err != nil || observed.Before(started) || observed.After(time.Now().UTC().Add(time.Second)) {
			t.Fatalf("invalid gateway receipt time for %s: %q (%v)", key, raw, err)
		}
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

	tm := pki.NewEnrollmentTokenManager("0123456789abcdef0123456789abcdef")
	rm := pki.NewRevocationManager()

	controlSecret := "0123456789abcdef0123456789abcdef"
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
	if os.Getenv("LABOPS_ENABLE_LIVE_TESTS") != "1" {
		t.Skip("loopback gRPC integration test requires an isolated live-test opt-in")
	}
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

func TestDuplicateSessionOldDisconnectDoesNotRemoveNewSession(t *testing.T) {
	sm := NewSessionManager()
	hello := &pb.NodeHello{NodeId: "track"}
	old := sm.RegisterSession(hello, nil)
	pending := make(chan *pb.CommandResponse, 1)
	old.pendingCommands["old-command"] = pending
	current := sm.RegisterSession(hello, nil)
	if old.Status != "OFFLINE" {
		t.Fatalf("old session status = %s", old.Status)
	}
	if sm.IsCurrent("track", old) {
		t.Fatal("old session still current")
	}
	if !sm.IsCurrent("track", current) {
		t.Fatal("new session not current")
	}
	sm.RemoveSessionIfCurrent("track", old)
	if !sm.IsCurrent("track", current) {
		t.Fatal("old disconnect marked the replacement offline")
	}
	if _, ok := <-pending; ok {
		t.Fatal("pending command was not closed on takeover")
	}
}

func TestDispatchFailsClosedAndDoesNotTrustActor(t *testing.T) {
	sm := NewSessionManager()
	sm.RegisterSession(&pb.NodeHello{NodeId: "track"}, nil)
	h := NewHTTPControlPlaneServer("127.0.0.1", 0, sm, nil,
		pki.NewEnrollmentTokenManager("0123456789abcdef0123456789abcdef"),
		pki.NewRevocationManager(), "control-secret-0123456789abcdef0", nil)

	body := bytes.NewBufferString(`{"node_id":"track","action":"NOT_ALLOWED","actor_id":"admin"}`)
	r := httptest.NewRequest(http.MethodPost, "/api/v1/gateway/dispatch", body)
	r.Header.Set("Authorization", "Bearer control-secret-0123456789abcdef0")
	w := httptest.NewRecorder()
	h.handleDispatch(w, r)
	if w.Code != http.StatusForbidden {
		t.Fatalf("unknown action status=%d body=%s", w.Code, w.Body.String())
	}

	body = bytes.NewBufferString(`{"node_id":"offline","action":"INSPECT_HOST","actor_id":"admin"}`)
	r = httptest.NewRequest(http.MethodPost, "/api/v1/gateway/dispatch", body)
	r.Header.Set("Authorization", "Bearer control-secret-0123456789abcdef0")
	w = httptest.NewRecorder()
	h.handleDispatch(w, r)
	if w.Code == http.StatusOK {
		t.Fatalf("offline command was reported successful: %s", w.Body.String())
	}

	r = httptest.NewRequest(http.MethodPost, "/api/v1/gateway/dispatch", bytes.NewBufferString(`{"node_id":"track","action":"INSPECT_HOST"}`))
	w = httptest.NewRecorder()
	h.handleDispatch(w, r)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated dispatch status=%d", w.Code)
	}
}

func TestGatewayDoesNotDispatchDestructiveActionsWithoutDelegatedUserAuthorization(t *testing.T) {
	if AllowedActions["TERMINATE_PROCESS"] {
		t.Fatal("machine control secret alone must not authorize process termination")
	}
}

func TestGatewayControlHTTPBindMustBeLoopback(t *testing.T) {
	for _, address := range []string{"127.0.0.1", "::1"} {
		if !IsLoopbackControlBind(address) {
			t.Fatalf("loopback address %q rejected", address)
		}
	}
	for _, address := range []string{"0.0.0.0", "::", "192.0.2.10", "localhost", ""} {
		if IsLoopbackControlBind(address) {
			t.Fatalf("non-literal or public address %q accepted", address)
		}
	}

	h := NewHTTPControlPlaneServer("0.0.0.0", 0, NewSessionManager(), nil,
		pki.NewEnrollmentTokenManager("0123456789abcdef0123456789abcdef"),
		pki.NewRevocationManager(), "control-secret-0123456789abcdef0", nil)
	if err := h.Start(); err == nil {
		t.Fatal("public control-plane bind reached ListenAndServe")
	}
}

func TestFailedRevokePersistenceStopsSessionAndReturnsError(t *testing.T) {
	path := filepath.Join(t.TempDir(), "missing", "revocations.json")
	rm := pki.NewRevocationManagerWithPersistence(path)
	sm := NewSessionManager()
	session := sm.RegisterSession(&pb.NodeHello{NodeId: "track"}, nil)
	h := NewHTTPControlPlaneServer("127.0.0.1", 0, sm, nil,
		pki.NewEnrollmentTokenManager("0123456789abcdef0123456789abcdef"), rm,
		"control-secret-0123456789abcdef0", nil)
	r := httptest.NewRequest(http.MethodPost, "/api/v1/gateway/revoke", bytes.NewBufferString(`{"node_id":"track","reason":"test"}`))
	r.Header.Set("Authorization", "Bearer control-secret-0123456789abcdef0")
	w := httptest.NewRecorder()
	h.handleRevoke(w, r)
	if w.Code != http.StatusInternalServerError {
		t.Fatalf("revoke status=%d body=%s", w.Code, w.Body.String())
	}
	if rm.IsNodeAllowed("track") {
		t.Fatal("failed revoke left node authorized in memory")
	}
	if sm.IsCurrent("track", session) {
		t.Fatal("failed revoke kept the session active")
	}
}
