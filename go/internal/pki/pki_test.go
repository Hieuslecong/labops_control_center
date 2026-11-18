package pki

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"math/big"
	"net"
	"os"
	"path/filepath"
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

	// Token ràng buộc NodeID (AT-04)
	boundToken := etm.GenerateToken("ws-target", 5*time.Minute)
	if etm.ConsumeTokenForNode(boundToken, "ws-attacker") {
		t.Fatal("Kỳ vọng ConsumeTokenForNode thất bại khi node ID không khớp (AT-04)")
	}
	if !etm.ConsumeTokenForNode(boundToken, "ws-target") {
		t.Fatal("Kỳ vọng ConsumeTokenForNode thành công với đúng node ID")
	}
}

func TestRevocationPersistenceAcrossRestart(t *testing.T) {
	tempDir, err := os.MkdirTemp("", "labops-revocation-test-*")
	if err != nil {
		t.Fatalf("Lỗi tạo tempdir: %v", err)
	}
	defer os.RemoveAll(tempDir)

	revFile := filepath.Join(tempDir, "revocations.json")

	// 1. Khởi tạo RevocationManager với file bền vững và thu hồi node "ws-bad-actor"
	rm1 := NewRevocationManagerWithPersistence(revFile)
	rm1.AllowNode("ws-good-actor")
	rm1.RevokeNode("ws-bad-actor", "Compromised credential")

	if rm1.IsNodeAllowed("ws-bad-actor") {
		t.Fatal("ws-bad-actor phải bị từ chối")
	}

	// 2. Mô phỏng Gateway restart hoàn toàn bằng cách tạo instance mới từ cùng file
	rm2 := NewRevocationManagerWithPersistence(revFile)

	// AT-05: Node đã revoke phải tiếp tục bị từ chối sau restart
	if rm2.IsNodeAllowed("ws-bad-actor") {
		t.Fatal("AT-05 Vi phạm: ws-bad-actor vẫn được phép sau khi Gateway restart!")
	}
	if !rm2.IsNodeAllowed("ws-good-actor") {
		t.Fatal("ws-good-actor phải tiếp tục được phép sau khi Gateway restart")
	}
}

func TestPKISecurityFailClosedAndRevocationInvariants(t *testing.T) {
	// 1. Empty allowlist MUST deny-by-default
	emptyRM := NewRevocationManager()
	if emptyRM.IsNodeAllowed("any-node") {
		t.Fatal("Empty allowlist must fail-closed (deny-by-default)")
	}

	// 2. Corrupt revocations.json MUST fail-closed and reject mutations
	tmpDir := t.TempDir()
	corruptFile := filepath.Join(tmpDir, "revocations.json")
	if err := os.WriteFile(corruptFile, []byte(`{"revoked_nodes": {corrupt`), 0600); err != nil {
		t.Fatal(err)
	}
	corruptRM := NewRevocationManagerWithPersistence(corruptFile)
	if !corruptRM.IsCorrupted() {
		t.Fatal("Expected corrupted flag to be true on invalid JSON")
	}
	if corruptRM.IsNodeAllowed("workstation") {
		t.Fatal("Corrupt PKI state MUST deny all nodes")
	}
	if err := corruptRM.AllowNode("workstation"); err == nil {
		t.Fatal("Corrupt PKI state MUST refuse AllowNode mutations")
	}

	// 3. Auto-unrevoke prevention & certificate serial/fingerprint permanence
	revFile := filepath.Join(tmpDir, "rev2.json")
	rm := NewRevocationManagerWithPersistence(revFile)
	if err := rm.AllowNode("node-a"); err != nil {
		t.Fatal(err)
	}
	ca, err := GenerateOrLoadCA(filepath.Join(tmpDir, "ca"))
	if err != nil {
		t.Fatal(err)
	}
	oldCert, err := IssueNodeCert(ca, "node-a")
	if err != nil {
		t.Fatal(err)
	}
	if err := rm.RevokeCertificate("node-a", oldCert.Cert.SerialNumber.Text(16), CertFingerprintSHA256(oldCert.Cert), "stolen key"); err != nil {
		t.Fatal(err)
	}
	if err := rm.RevokeNode("node-a", "host compromised"); err != nil {
		t.Fatal(err)
	}
	if err := rm.AllowNode("node-a"); err == nil {
		t.Fatal("AllowNode MUST reject automatically un-revoking a revoked node")
	}
	if err := rm.UnrevokeNode("node-a", "sec-admin", "hardware wiped"); err != nil {
		t.Fatalf("Explicit UnrevokeNode failed: %v", err)
	}
	// Stolen old certificate serial MUST still be rejected even after node un-revoke
	if err := rm.IsCertAllowed(oldCert.Cert); err == nil {
		t.Fatal("Revoked certificate serial/fingerprint MUST remain denied after UnrevokeNode")
	}
	newCert, err := IssueNodeCert(ca, "node-a")
	if err != nil {
		t.Fatal(err)
	}
	if err := rm.IsCertAllowed(newCert.Cert); err != nil {
		t.Fatalf("Freshly issued certificate for un-revoked node-a should be allowed: %v", err)
	}

	// 4. Empty enrollment token manager secret MUST fail-closed
	emptyETM := NewEnrollmentTokenManager("")
	if tok := emptyETM.GenerateToken("node-a", time.Minute); tok != "" {
		t.Fatal("EnrollmentTokenManager with empty secret must return empty token")
	}
	if emptyETM.ConsumeToken("anything") || emptyETM.ConsumeTokenForNode("anything", "node-a") {
		t.Fatal("EnrollmentTokenManager with empty secret must reject all consume attempts")
	}

	// 5. ExtractNodeIDFromCert rejects CN without SAN and rejects CN/SAN mismatch
	priv, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	cnOnlyTpl := &x509.Certificate{
		SerialNumber: big.NewInt(999),
		Subject:      pkix.Name{CommonName: "spoof-node"},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(time.Hour),
	}
	cnOnlyDER, _ := x509.CreateCertificate(rand.Reader, cnOnlyTpl, ca.Cert, &priv.PublicKey, ca.Key)
	cnOnlyCert, _ := x509.ParseCertificate(cnOnlyDER)
	if _, err := ExtractNodeIDFromCert(cnOnlyCert); err == nil {
		t.Fatal("ExtractNodeIDFromCert MUST reject CN-only cert lacking .node.labops.local SAN")
	}

	mismatchTpl := &x509.Certificate{
		SerialNumber: big.NewInt(1000),
		Subject:      pkix.Name{CommonName: "wrong-cn"},
		DNSNames:     []string{"node-a" + NodeSANPrefix},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(time.Hour),
	}
	mismatchDER, _ := x509.CreateCertificate(rand.Reader, mismatchTpl, ca.Cert, &priv.PublicKey, ca.Key)
	mismatchCert, _ := x509.ParseCertificate(mismatchDER)
	if _, err := ExtractNodeIDFromCert(mismatchCert); err == nil {
		t.Fatal("ExtractNodeIDFromCert MUST reject cert where CommonName != SAN nodeID")
	}
}
