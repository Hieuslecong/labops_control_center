package pki

import (
	"crypto/tls"
	"net"
	"os"
	"testing"
	"time"
)

func TestPKIWorkflowAndTLSHandshake(t *testing.T) {
	tempDir, err := os.MkdirTemp("", "labops-pki-test-*")
	if err != nil {
		t.Fatalf("Không thể tạo temp dir: %v", err)
	}
	defer os.RemoveAll(tempDir)

	// 1. Khởi tạo CA
	ca, err := GenerateOrLoadCA(tempDir)
	if err != nil {
		t.Fatalf("Lỗi GenerateOrLoadCA: %v", err)
	}
	if ca == nil || ca.Cert == nil {
		t.Fatal("CA cert rỗng")
	}

	// 2. Nạp lại CA từ disk để kiểm tra tính toàn vẹn
	loadedCA, err := GenerateOrLoadCA(tempDir)
	if err != nil {
		t.Fatalf("Lỗi load lại CA: %v", err)
	}
	if loadedCA.Cert.SerialNumber.Cmp(ca.Cert.SerialNumber) != 0 {
		t.Fatal("Serial number của CA đã nạp không khớp")
	}

	// 3. Phát hành chứng chỉ Gateway
	gwCert, err := IssueGatewayCert(ca, []string{"127.0.0.1", "localhost", "aiotlab-rack"})
	if err != nil {
		t.Fatalf("Lỗi IssueGatewayCert: %v", err)
	}

	// 4. Phát hành chứng chỉ Node
	nodeID := "ws-testnode-1"
	nodeCert, err := IssueNodeCert(ca, nodeID)
	if err != nil {
		t.Fatalf("Lỗi IssueNodeCert: %v", err)
	}

	extractedID, err := ExtractNodeIDFromCert(nodeCert.Cert)
	if err != nil {
		t.Fatalf("Lỗi ExtractNodeIDFromCert: %v", err)
	}
	if extractedID != nodeID {
		t.Fatalf("Node ID trích xuất '%s' != mong muốn '%s'", extractedID, nodeID)
	}

	// 5. Kiểm tra Revocation Manager
	rm := NewRevocationManager()
	rm.AllowNode(nodeID)

	serverTLS, err := BuildServerTLSConfig(ca.CertPEM, gwCert.CertPEM, gwCert.KeyPEM, rm)
	if err != nil {
		t.Fatalf("Lỗi BuildServerTLSConfig: %v", err)
	}

	clientTLS, err := BuildClientTLSConfig(ca.CertPEM, nodeCert.CertPEM, nodeCert.KeyPEM, "localhost")
	if err != nil {
		t.Fatalf("Lỗi BuildClientTLSConfig: %v", err)
	}

	// 6. Thực hiện Handshake mTLS qua loopback TCP
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("Không thể tạo TCP listener: %v", err)
	}
	defer listener.Close()

	doneAccept := make(chan struct{})
	go func() {
		defer close(doneAccept)
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		defer conn.Close()
		tlsSrv := tls.Server(conn, serverTLS)
		_ = tlsSrv.Handshake()
	}()

	clientRawConn, err := net.Dial("tcp", listener.Addr().String())
	if err != nil {
		t.Fatalf("Không thể kết nối TCP client: %v", err)
	}
	tlsCli := tls.Client(clientRawConn, clientTLS)
	if err := tlsCli.Handshake(); err != nil {
		t.Fatalf("Lỗi mTLS handshake thành công kỳ vọng nhưng bị lỗi: %v", err)
	}
	state := tlsCli.ConnectionState()
	if state.Version != tls.VersionTLS13 {
		t.Fatalf("Mong muốn TLS 1.3 nhưng nhận được: %x", state.Version)
	}
	clientRawConn.Close()
	<-doneAccept

	// 7. Thử nghiệm thu hồi chứng chỉ (Revocation check)
	rm.RevokeNode(nodeID, "Bị nghi ngờ xâm phạm")

	listenerRevoked, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("Không thể tạo TCP listener: %v", err)
	}
	defer listenerRevoked.Close()

	srvErrCh := make(chan error, 1)
	go func() {
		conn, err := listenerRevoked.Accept()
		if err != nil {
			srvErrCh <- err
			return
		}
		defer conn.Close()
		tlsSrv := tls.Server(conn, serverTLS)
		srvErrCh <- tlsSrv.Handshake()
	}()

	clientRawConn2, err := net.Dial("tcp", listenerRevoked.Addr().String())
	if err != nil {
		t.Fatalf("Không thể kết nối TCP: %v", err)
	}
	defer clientRawConn2.Close()
	tlsCli2 := tls.Client(clientRawConn2, clientTLS)
	cliErr := tlsCli2.Handshake()
	srvErr := <-srvErrCh

	if srvErr == nil && cliErr == nil {
		t.Fatal("Kỳ vọng mTLS handshake thất bại do node đã bị thu hồi nhưng cả server và client đều thành công")
	}
	t.Logf("Revocation rejected as expected - Server: %v, Client: %v", srvErr, cliErr)
}

func TestEnrollmentTokenManager(t *testing.T) {
	etm := NewEnrollmentTokenManager("test-salt")
	token := etm.GenerateToken("ws-compute-1", 1*time.Minute)
	if token == "" {
		t.Fatal("Token rỗng")
	}

	// Lần tiêu thụ 1: Phải thành công
	if !etm.ConsumeToken(token) {
		t.Fatal("Kỳ vọng ConsumeToken thành công lần đầu")
	}

	// Lần tiêu thụ 2 (replay): Phải thất bại
	if etm.ConsumeToken(token) {
		t.Fatal("Kỳ vọng ConsumeToken thất bại khi replay")
	}

	// Token hết hạn
	expToken := etm.GenerateToken("ws-compute-2", -1*time.Second)
	if etm.ConsumeToken(expToken) {
		t.Fatal("Kỳ vọng ConsumeToken thất bại do token đã hết hạn")
	}
}
