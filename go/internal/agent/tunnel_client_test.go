package agent

import (
	"context"
	"net"
	"os"
	"testing"
	"time"

	"github.com/Hieuslecong/labops_control_center/go/internal/config"
	"github.com/Hieuslecong/labops_control_center/go/internal/docker"
	"github.com/Hieuslecong/labops_control_center/go/internal/gateway"
	"github.com/Hieuslecong/labops_control_center/go/internal/gpu"
	"github.com/Hieuslecong/labops_control_center/go/internal/host"
	"github.com/Hieuslecong/labops_control_center/go/internal/pki"
	"github.com/Hieuslecong/labops_control_center/go/internal/process"
)

func TestTunnelClientEndToEndWithGateway(t *testing.T) {
	tempDir, err := os.MkdirTemp("", "labops-agent-tunnel-test-*")
	if err != nil {
		t.Fatalf("Lỗi tạo tempdir: %v", err)
	}
	defer os.RemoveAll(tempDir)

	// 1. Sinh Root CA
	ca, err := pki.GenerateOrLoadCA(tempDir)
	if err != nil {
		t.Fatalf("Lỗi CA: %v", err)
	}

	// 2. Cấp chứng chỉ cho Gateway
	gwCert, err := pki.IssueGatewayCert(ca, []string{"127.0.0.1", "localhost"})
	if err != nil {
		t.Fatalf("Lỗi issue gateway cert: %v", err)
	}

	// 3. Cấp chứng chỉ cho Node Agent
	nodeID := "ws-e2e-node"
	nodeCert, err := pki.IssueNodeCert(ca, nodeID)
	if err != nil {
		t.Fatalf("Lỗi issue node cert: %v", err)
	}

	// Lưu chứng chỉ node ra disk
	caFile := tempDir + "/ca.crt"
	clientCertFile := tempDir + "/client.crt"
	clientKeyFile := tempDir + "/client.key"
	_ = os.WriteFile(caFile, ca.CertPEM, 0644)
	_ = os.WriteFile(clientCertFile, nodeCert.CertPEM, 0644)
	_ = os.WriteFile(clientKeyFile, nodeCert.KeyPEM, 0600)

	rm := pki.NewRevocationManager()
	rm.AllowNode(nodeID)

	serverTLS, err := pki.BuildServerTLSConfig(ca.CertPEM, gwCert.CertPEM, gwCert.KeyPEM, rm)
	if err != nil {
		t.Fatalf("Lỗi server TLS: %v", err)
	}

	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("Lỗi listen: %v", err)
	}
	defer lis.Close()

	port := lis.Addr().(*net.TCPAddr).Port
	gwConfig := &gateway.Config{
		BindAddr:          "127.0.0.1",
		GRPCPort:          port,
		RevocationManager: rm,
	}

	gwServer, err := gateway.NewServer(gwConfig, ca, serverTLS)
	if err != nil {
		t.Fatalf("Lỗi NewServer: %v", err)
	}

	go func() {
		_ = gwServer.Serve(lis)
	}()
	defer gwServer.Stop()

	// 4. Khởi tạo Agent và TunnelClient
	cfg := &config.AgentConfig{
		ServerID:          nodeID,
		Hostname:          "test-agent-host",
		BindAddr:          "127.0.0.1",
		GatewayEnabled:    true,
		GatewayURL:        lis.Addr().String(),
		GatewayCAPath:     caFile,
		GatewayCertPath:   clientCertFile,
		GatewayKeyPath:    clientKeyFile,
		GatewayServerName: "localhost",
		HostInterval:      200 * time.Millisecond,
		ProcessInterval:   500 * time.Millisecond,
	}

	hostCol := host.NewCollector(cfg)
	gpuCol := gpu.NewCollector()
	procCol := process.NewCollector(nodeID, gpuCol)
	procCtrl := process.NewController(procCol)
	dockerCtrl := docker.NewController("/var/run/docker.sock")

	client := NewTunnelClient(cfg, hostCol, gpuCol, procCol, procCtrl, dockerCtrl, nil)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	client.Start(ctx)

	// Đợi Agent kết nối và đăng ký thành công trên Gateway
	var sess *gateway.NodeSession
	for i := 0; i < 20; i++ {
		time.Sleep(100 * time.Millisecond)
		s, ok := gwServer.SessionManager().GetSession(nodeID)
		if ok && s.Status == "ONLINE" {
			sess = s
			break
		}
	}

	if sess == nil {
		t.Fatalf("Timeout: Agent chưa đăng ký hoặc trạng thái không ONLINE trên Gateway")
	}

	// 5. Thử nghiệm Gateway Dispatch Command "INSPECT_HOST" tới Agent qua stream
	dispResp, err := gwServer.SessionManager().DispatchCommand(context.Background(), &gateway.DispatchRequest{
		NodeID:         nodeID,
		Action:         "INSPECT_HOST",
		TimeoutSeconds: 5,
	})
	if err != nil {
		t.Fatalf("Lỗi DispatchCommand: %v", err)
	}
	if !dispResp.Success {
		t.Fatalf("Lệnh INSPECT_HOST thất bại: %+v", dispResp)
	}
	t.Logf("Dispatch INSPECT_HOST thành công trong %d ms", dispResp.DurationMs)

	// 6. Thử nghiệm Dispatch Command "COLLECT_TELEMETRY"
	teleResp, err := gwServer.SessionManager().DispatchCommand(context.Background(), &gateway.DispatchRequest{
		NodeID:         nodeID,
		Action:         "COLLECT_TELEMETRY",
		TimeoutSeconds: 5,
	})
	if err != nil {
		t.Fatalf("Lỗi DispatchCommand: %v", err)
	}
	if !teleResp.Success {
		t.Fatalf("Lệnh COLLECT_TELEMETRY thất bại: %+v", teleResp)
	}
	t.Logf("Dispatch COLLECT_TELEMETRY thành công trong %d ms", teleResp.DurationMs)
}
