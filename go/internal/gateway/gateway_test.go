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

	httpSrv := NewHTTPControlPlaneServer("127.0.0.1", 0, sm, ca, tm, rm, nil)

	// 1. Test /health
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	httpSrv.handleHealth(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("Health check lỗi: %d", w.Code)
	}

	// 2. Test /api/v1/gateway/nodes
	req = httptest.NewRequest(http.MethodGet, "/api/v1/gateway/nodes", nil)
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

	// 3. Test /api/v1/gateway/enroll
	enrollBody, _ := json.Marshal(map[string]any{
		"node_id":     "ws-node-new",
		"ttl_minutes": 10,
	})
	req = httptest.NewRequest(http.MethodPost, "/api/v1/gateway/enroll", bytes.NewReader(enrollBody))
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

	// 4. Test /api/v1/gateway/issue-cert với token vừa tạo
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
