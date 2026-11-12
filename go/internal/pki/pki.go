package pki

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const (
	NodeSANPrefix     = ".node.labops.local"
	DefaultCAValidity = 10 * 365 * 24 * time.Hour
	DefaultCertValid  = 365 * 24 * time.Hour
)

// CertBundle chứa chứng chỉ và private key dạng PEM
type CertBundle struct {
	CertPEM []byte
	KeyPEM  []byte
	Cert    *x509.Certificate
}

// CACertificate đại diện cho LabOps Root CA
type CACertificate struct {
	Cert       *x509.Certificate
	Key        *ecdsa.PrivateKey
	CertPEM    []byte
	KeyPEM     []byte
	CACertPath string
	CAKeyPath  string
}

// RevocationManager quản lý danh sách thu hồi node certificate
type RevocationManager struct {
	mu           sync.RWMutex
	revokedNodes map[string]string // node_id -> reason
	allowedNodes map[string]bool   // node_id -> allowed
}

func NewRevocationManager() *RevocationManager {
	return &RevocationManager{
		revokedNodes: make(map[string]string),
		allowedNodes: make(map[string]bool),
	}
}

func (rm *RevocationManager) AllowNode(nodeID string) {
	rm.mu.Lock()
	defer rm.mu.Unlock()
	rm.allowedNodes[nodeID] = true
	delete(rm.revokedNodes, nodeID)
}

func (rm *RevocationManager) RevokeNode(nodeID string, reason string) {
	rm.mu.Lock()
	defer rm.mu.Unlock()
	delete(rm.allowedNodes, nodeID)
	rm.revokedNodes[nodeID] = reason
}

func (rm *RevocationManager) IsNodeAllowed(nodeID string) bool {
	rm.mu.RLock()
	defer rm.mu.RUnlock()
	if _, revoked := rm.revokedNodes[nodeID]; revoked {
		return false
	}
	if len(rm.allowedNodes) > 0 {
		return rm.allowedNodes[nodeID]
	}
	return true
}

// GenerateOrLoadCA khởi tạo hoặc nạp CA nội bộ LabOps
func GenerateOrLoadCA(caDir string) (*CACertificate, error) {
	if err := os.MkdirAll(caDir, 0700); err != nil {
		return nil, fmt.Errorf("không thể tạo thư mục CA: %w", err)
	}

	certPath := filepath.Join(caDir, "labops-ca.crt")
	keyPath := filepath.Join(caDir, "labops-ca.key")

	if fileExists(certPath) && fileExists(keyPath) {
		return loadCA(certPath, keyPath)
	}

	privKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("không thể tạo private key CA: %w", err)
	}

	serialNumber, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return nil, fmt.Errorf("không thể tạo serial number: %w", err)
	}

	template := &x509.Certificate{
		SerialNumber: serialNumber,
		Subject: pkix.Name{
			Organization:  []string{"LabOps AI Infrastructure"},
			OrganizationalUnit: []string{"Security Root CA"},
			CommonName:    "LabOps Control Center Root CA (V2)",
		},
		NotBefore:             time.Now().Add(-1 * time.Hour),
		NotAfter:              time.Now().Add(DefaultCAValidity),
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageCRLSign,
		BasicConstraintsValid: true,
		IsCA:                  true,
		MaxPathLen:            1,
	}

	certBytes, err := x509.CreateCertificate(rand.Reader, template, template, &privKey.PublicKey, privKey)
	if err != nil {
		return nil, fmt.Errorf("không thể ký chứng chỉ CA: %w", err)
	}

	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certBytes})
	keyBytes, err := x509.MarshalECPrivateKey(privKey)
	if err != nil {
		return nil, fmt.Errorf("không thể mã hóa private key: %w", err)
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyBytes})

	if err := os.WriteFile(certPath, certPEM, 0644); err != nil {
		return nil, fmt.Errorf("không thể lưu ca.crt: %w", err)
	}
	if err := os.WriteFile(keyPath, keyPEM, 0600); err != nil {
		return nil, fmt.Errorf("không thể lưu ca.key: %w", err)
	}

	parsedCert, err := x509.ParseCertificate(certBytes)
	if err != nil {
		return nil, err
	}

	return &CACertificate{
		Cert:       parsedCert,
		Key:        privKey,
		CertPEM:    certPEM,
		KeyPEM:     keyPEM,
		CACertPath: certPath,
		CAKeyPath:  keyPath,
	}, nil
}

func loadCA(certPath, keyPath string) (*CACertificate, error) {
	certPEM, err := os.ReadFile(certPath)
	if err != nil {
		return nil, fmt.Errorf("lỗi đọc ca.crt: %w", err)
	}
	keyPEM, err := os.ReadFile(keyPath)
	if err != nil {
		return nil, fmt.Errorf("lỗi đọc ca.key: %w", err)
	}

	block, _ := pem.Decode(certPEM)
	if block == nil {
		return nil, errors.New("không thể decode ca.crt PEM")
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("lỗi parse CA cert: %w", err)
	}

	keyBlock, _ := pem.Decode(keyPEM)
	if keyBlock == nil {
		return nil, errors.New("không thể decode ca.key PEM")
	}
	key, err := x509.ParseECPrivateKey(keyBlock.Bytes)
	if err != nil {
		return nil, fmt.Errorf("lỗi parse CA private key: %w", err)
	}

	return &CACertificate{
		Cert:       cert,
		Key:        key,
		CertPEM:    certPEM,
		KeyPEM:     keyPEM,
		CACertPath: certPath,
		CAKeyPath:  keyPath,
	}, nil
}

// IssueGatewayCert phát hành chứng chỉ Server mTLS cho Go Agent Gateway
func IssueGatewayCert(ca *CACertificate, hosts []string) (*CertBundle, error) {
	privKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("lỗi tạo key gateway: %w", err)
	}

	serialNumber, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return nil, err
	}

	template := &x509.Certificate{
		SerialNumber: serialNumber,
		Subject: pkix.Name{
			Organization: []string{"LabOps AI Infrastructure"},
			CommonName:   "gateway.control-plane.labops.local",
		},
		NotBefore:   time.Now().Add(-1 * time.Hour),
		NotAfter:    time.Now().Add(DefaultCertValid),
		KeyUsage:    x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}

	for _, h := range hosts {
		if ip := net.ParseIP(h); ip != nil {
			template.IPAddresses = append(template.IPAddresses, ip)
		} else {
			template.DNSNames = append(template.DNSNames, h)
		}
	}
	template.DNSNames = append(template.DNSNames, "gateway.control-plane.labops.local", "localhost", "aiotlab-rack")
	template.IPAddresses = append(template.IPAddresses, net.ParseIP("127.0.0.1"), net.ParseIP("192.168.167.250"))

	certBytes, err := x509.CreateCertificate(rand.Reader, template, ca.Cert, &privKey.PublicKey, ca.Key)
	if err != nil {
		return nil, fmt.Errorf("lỗi ký chứng chỉ gateway: %w", err)
	}

	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certBytes})
	keyBytes, err := x509.MarshalECPrivateKey(privKey)
	if err != nil {
		return nil, err
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyBytes})

	parsedCert, _ := x509.ParseCertificate(certBytes)
	return &CertBundle{
		CertPEM: certPEM,
		KeyPEM:  keyPEM,
		Cert:    parsedCert,
	}, nil
}

// IssueNodeCert phát hành chứng chỉ Client mTLS cho một Node Agent cụ thể
func IssueNodeCert(ca *CACertificate, nodeID string) (*CertBundle, error) {
	nodeID = strings.TrimSpace(strings.ToLower(nodeID))
	if nodeID == "" {
		return nil, errors.New("nodeID không được rỗng")
	}

	privKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("lỗi tạo key node: %w", err)
	}

	serialNumber, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return nil, err
	}

	nodeSAN := nodeID + NodeSANPrefix
	template := &x509.Certificate{
		SerialNumber: serialNumber,
		Subject: pkix.Name{
			Organization: []string{"LabOps AI Compute Nodes"},
			CommonName:   nodeID,
		},
		NotBefore:   time.Now().Add(-1 * time.Hour),
		NotAfter:    time.Now().Add(DefaultCertValid),
		KeyUsage:    x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
		DNSNames:    []string{nodeSAN, nodeID},
	}

	certBytes, err := x509.CreateCertificate(rand.Reader, template, ca.Cert, &privKey.PublicKey, ca.Key)
	if err != nil {
		return nil, fmt.Errorf("lỗi ký chứng chỉ node %s: %w", nodeID, err)
	}

	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certBytes})
	keyBytes, err := x509.MarshalECPrivateKey(privKey)
	if err != nil {
		return nil, err
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyBytes})

	parsedCert, _ := x509.ParseCertificate(certBytes)
	return &CertBundle{
		CertPEM: certPEM,
		KeyPEM:  keyPEM,
		Cert:    parsedCert,
	}, nil
}

// ExtractNodeIDFromCert trích xuất Node ID từ SAN của Client Certificate
func ExtractNodeIDFromCert(cert *x509.Certificate) (string, error) {
	if cert == nil {
		return "", errors.New("chứng chỉ rỗng")
	}

	// 1. Tìm trong DNSNames theo chuẩn <node_id>.node.labops.local
	for _, dns := range cert.DNSNames {
		if strings.HasSuffix(dns, NodeSANPrefix) {
			nodeID := strings.TrimSuffix(dns, NodeSANPrefix)
			if nodeID != "" {
				return nodeID, nil
			}
		}
	}

	// 2. Fallback sang CommonName
	if cert.Subject.CommonName != "" {
		return cert.Subject.CommonName, nil
	}

	return "", errors.New("chứng chỉ client không chứa Node ID hợp lệ trong SAN hoặc CommonName")
}

// BuildServerTLSConfig thiết lập cấu hình TLS 1.3 mTLS cho Gateway
func BuildServerTLSConfig(caPEM, serverCertPEM, serverKeyPEM []byte, rm *RevocationManager) (*tls.Config, error) {
	cert, err := tls.X509KeyPair(serverCertPEM, serverKeyPEM)
	if err != nil {
		return nil, fmt.Errorf("lỗi nạp keypair server: %w", err)
	}

	caPool := x509.NewCertPool()
	if !caPool.AppendCertsFromPEM(caPEM) {
		return nil, errors.New("không thể nạp CA PEM vào cert pool")
	}

	tlsCfg := &tls.Config{
		Certificates: []tls.Certificate{cert},
		ClientCAs:    caPool,
		ClientAuth:   tls.RequireAndVerifyClientCert,
		MinVersion:   tls.VersionTLS13,
		VerifyPeerCertificate: func(rawCerts [][]byte, verifiedChains [][]*x509.Certificate) error {
			if len(verifiedChains) == 0 || len(verifiedChains[0]) == 0 {
				return errors.New("mTLS: không có chuỗi chứng chỉ được xác minh")
			}
			clientCert := verifiedChains[0][0]
			nodeID, err := ExtractNodeIDFromCert(clientCert)
			if err != nil {
				return fmt.Errorf("mTLS: %w", err)
			}

			if rm != nil && !rm.IsNodeAllowed(nodeID) {
				return fmt.Errorf("mTLS: Node ID '%s' đã bị từ chối hoặc thu hồi quyền truy cập", nodeID)
			}
			return nil
		},
	}

	return tlsCfg, nil
}

// BuildClientTLSConfig thiết lập cấu hình TLS 1.3 mTLS cho Node Agent Client
func BuildClientTLSConfig(caPEM, clientCertPEM, clientKeyPEM []byte, serverName string) (*tls.Config, error) {
	cert, err := tls.X509KeyPair(clientCertPEM, clientKeyPEM)
	if err != nil {
		return nil, fmt.Errorf("lỗi nạp keypair client: %w", err)
	}

	caPool := x509.NewCertPool()
	if !caPool.AppendCertsFromPEM(caPEM) {
		return nil, errors.New("không thể nạp CA PEM vào client pool")
	}

	tlsCfg := &tls.Config{
		Certificates: []tls.Certificate{cert},
		RootCAs:      caPool,
		ServerName:   serverName,
		MinVersion:   tls.VersionTLS13,
	}

	return tlsCfg, nil
}

// One-Time Enrollment Token
type EnrollmentTokenManager struct {
	mu     sync.Mutex
	secret []byte
	tokens map[string]int64 // token -> expires_at_unix
}

func NewEnrollmentTokenManager(secret string) *EnrollmentTokenManager {
	if secret == "" {
		secret = "labops-enrollment-default-secret-salt"
	}
	return &EnrollmentTokenManager{
		secret: []byte(secret),
		tokens: make(map[string]int64),
	}
}

func (etm *EnrollmentTokenManager) GenerateToken(nodeID string, ttl time.Duration) string {
	etm.mu.Lock()
	defer etm.mu.Unlock()

	randBytes := make([]byte, 16)
	_, _ = rand.Read(randBytes)
	h := sha256.New()
	h.Write(etm.secret)
	h.Write([]byte(nodeID))
	h.Write(randBytes)
	token := hex.EncodeToString(h.Sum(nil))

	expiresAt := time.Now().Add(ttl).Unix()
	etm.tokens[token] = expiresAt
	return token
}

func (etm *EnrollmentTokenManager) ConsumeToken(token string) bool {
	etm.mu.Lock()
	defer etm.mu.Unlock()

	exp, exists := etm.tokens[token]
	if !exists {
		return false
	}
	delete(etm.tokens, token)
	return time.Now().Unix() <= exp
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}
