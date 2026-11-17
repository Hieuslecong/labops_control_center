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
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
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

// RevocationData cấu trúc lưu trữ danh sách thu hồi trên đĩa
type RevocationData struct {
	RevokedNodes map[string]string `json:"revoked_nodes"`
	AllowedNodes map[string]bool   `json:"allowed_nodes"`
	UpdatedAt    string            `json:"updated_at"`
}

// RevocationManager quản lý danh sách thu hồi node certificate có hỗ trợ lưu trữ bền vững
type RevocationManager struct {
	mu           sync.RWMutex
	filePath     string
	loadErr      error
	persistErr   error
	persistHook  func(string) error
	statePresent bool
	revokedNodes map[string]string // node_id -> reason
	allowedNodes map[string]bool   // node_id -> allowed
}

func NewRevocationManager() *RevocationManager {
	return NewRevocationManagerWithPersistence("")
}

func NewRevocationManagerWithPersistence(filePath string) *RevocationManager {
	rm := &RevocationManager{
		filePath:     filePath,
		revokedNodes: make(map[string]string),
		allowedNodes: make(map[string]bool),
	}
	if filePath != "" {
		rm.loadFromDisk()
	}
	return rm
}

func (rm *RevocationManager) loadFromDisk() {
	if rm.filePath == "" {
		return
	}
	if _, err := os.Lstat(rm.filePath + ".pending"); err == nil {
		rm.loadErr = errors.New("revocation update was interrupted; manual fail-closed recovery is required")
		return
	} else if !errors.Is(err, os.ErrNotExist) {
		rm.loadErr = err
		return
	}
	info, err := os.Lstat(rm.filePath)
	if errors.Is(err, os.ErrNotExist) {
		return
	}
	if err != nil {
		rm.loadErr = err
		return
	}
	rm.statePresent = true
	if !info.Mode().IsRegular() {
		rm.loadErr = errors.New("revocation state must be a regular file")
		return
	}
	data, err := os.ReadFile(rm.filePath)
	if errors.Is(err, os.ErrNotExist) {
		return
	}
	if err != nil {
		rm.loadErr = err
		return
	}
	var rd RevocationData
	if err := json.Unmarshal(data, &rd); err != nil {
		rm.loadErr = err
		return
	}
	if rd.AllowedNodes == nil || rd.RevokedNodes == nil {
		rm.loadErr = errors.New("invalid revocation state")
		return
	}
	rm.allowedNodes, rm.revokedNodes = rd.AllowedNodes, rd.RevokedNodes
}

func (rm *RevocationManager) LoadError() error {
	rm.mu.RLock()
	defer rm.mu.RUnlock()
	if rm.loadErr != nil {
		return rm.loadErr
	}
	return rm.persistErr
}

// StatePresent reports whether a persisted allowlist already existed at startup.
func (rm *RevocationManager) StatePresent() bool {
	rm.mu.RLock()
	defer rm.mu.RUnlock()
	return rm.statePresent
}

// BootstrapAllowedNodes is a one-time explicit bootstrap for a missing state file.
// It never merges into or overwrites an existing allowlist.
func (rm *RevocationManager) BootstrapAllowedNodes(nodeIDs []string) error {
	if rm.filePath == "" {
		return errors.New("persistent revocation state is required for bootstrap")
	}
	if len(nodeIDs) == 0 {
		return errors.New("bootstrap allowlist must contain at least one node")
	}
	rm.mu.Lock()
	defer rm.mu.Unlock()
	if rm.loadErr != nil {
		return rm.loadErr
	}
	if rm.persistErr != nil {
		return rm.persistErr
	}
	if rm.statePresent || len(rm.allowedNodes) != 0 || len(rm.revokedNodes) != 0 {
		return errors.New("bootstrap is allowed only when no persisted state exists")
	}
	bootstrap := make(map[string]bool, len(nodeIDs))
	for _, raw := range nodeIDs {
		nodeID, err := NormalizeNodeID(raw)
		if err != nil {
			return err
		}
		bootstrap[nodeID] = true
	}
	if len(bootstrap) == 0 {
		return errors.New("bootstrap allowlist must contain at least one valid node")
	}
	rm.allowedNodes = bootstrap
	if err := rm.saveToDiskLocked(); err != nil {
		rm.persistErr = err
		return err
	}
	rm.statePresent = true
	return nil
}

func (rm *RevocationManager) saveToDiskLocked() error {
	if rm.filePath == "" {
		return nil
	}
	data, err := json.MarshalIndent(RevocationData{RevokedNodes: rm.revokedNodes, AllowedNodes: rm.allowedNodes, UpdatedAt: time.Now().UTC().Format(time.RFC3339)}, "", "  ")
	if err != nil {
		return err
	}
	dir := filepath.Dir(rm.filePath)
	// A durable pending marker makes a crash or failed write fail closed on the
	// next startup. If the filesystem cannot create this marker at all, durability
	// cannot be guaranteed; the running manager still latches persistErr and denies
	// every node, and the caller receives an error.
	markerPath := rm.filePath + ".pending"
	marker, err := os.OpenFile(markerPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return fmt.Errorf("create revocation recovery marker: %w", err)
	}
	if _, err = marker.Write([]byte("revocation update pending\n")); err == nil {
		err = marker.Sync()
	}
	if closeErr := marker.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return fmt.Errorf("persist revocation recovery marker: %w", err)
	}
	dirHandle, err := os.Open(dir)
	if err != nil {
		return fmt.Errorf("open revocation directory for sync: %w", err)
	}
	if err = dirHandle.Sync(); err != nil {
		_ = dirHandle.Close()
		return fmt.Errorf("sync revocation recovery marker: %w", err)
	}
	if err = dirHandle.Close(); err != nil {
		return fmt.Errorf("close revocation directory: %w", err)
	}
	if rm.persistHook != nil {
		if err := rm.persistHook("create"); err != nil {
			return err
		}
	}
	f, err := os.CreateTemp(dir, ".revocations-*")
	if err != nil {
		return err
	}
	name := f.Name()
	defer os.Remove(name)
	if err = f.Chmod(0600); err == nil && rm.persistHook != nil {
		err = rm.persistHook("write")
	}
	if err == nil {
		var n int
		n, err = f.Write(data)
		if err == nil && n != len(data) {
			err = io.ErrShortWrite
		}
	}
	if err == nil && rm.persistHook != nil {
		err = rm.persistHook("file_sync")
	}
	if err == nil {
		err = f.Sync()
	}
	if closeErr := f.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	if rm.persistHook != nil {
		if err = rm.persistHook("rename"); err != nil {
			return err
		}
	}
	if err = os.Rename(name, rm.filePath); err != nil {
		return err
	}
	d, err := os.Open(dir)
	if err != nil {
		return err
	}
	if rm.persistHook != nil {
		if err = rm.persistHook("dir_sync"); err != nil {
			return err
		}
	}
	if err := d.Sync(); err != nil {
		return err
	}
	if err := d.Close(); err != nil {
		return err
	}
	if err := os.Remove(markerPath); err != nil {
		return fmt.Errorf("remove revocation recovery marker: %w", err)
	}
	d, err = os.Open(dir)
	if err != nil {
		return fmt.Errorf("reopen revocation directory after marker removal: %w", err)
	}
	if err := d.Sync(); err != nil {
		_ = d.Close()
		return err
	}
	if err := d.Close(); err != nil {
		return err
	}
	rm.statePresent = true
	return nil
}

func (rm *RevocationManager) AllowNode(nodeID string) error {
	nodeID, err := NormalizeNodeID(nodeID)
	if err != nil {
		return err
	}
	rm.mu.Lock()
	defer rm.mu.Unlock()
	if rm.loadErr != nil {
		return rm.loadErr
	}
	if rm.persistErr != nil {
		return rm.persistErr
	}
	if _, revoked := rm.revokedNodes[nodeID]; revoked {
		return errors.New("revoked node cannot be enrolled")
	}
	oldAllowed, hadAllowed := rm.allowedNodes[nodeID]
	oldReason, hadRevoked := rm.revokedNodes[nodeID]
	rm.allowedNodes[nodeID] = true
	delete(rm.revokedNodes, nodeID)
	if err := rm.saveToDiskLocked(); err != nil {
		if hadAllowed {
			rm.allowedNodes[nodeID] = oldAllowed
		} else {
			delete(rm.allowedNodes, nodeID)
		}
		if hadRevoked {
			rm.revokedNodes[nodeID] = oldReason
		}
		rm.persistErr = err
		return err
	}
	return nil
}

func (rm *RevocationManager) RevokeNode(nodeID string, reason string) error {
	nodeID, err := NormalizeNodeID(nodeID)
	if err != nil {
		return err
	}
	rm.mu.Lock()
	defer rm.mu.Unlock()
	if rm.loadErr != nil {
		return rm.loadErr
	}
	if rm.persistErr != nil {
		return rm.persistErr
	}
	delete(rm.allowedNodes, nodeID)
	rm.revokedNodes[nodeID] = reason
	if err := rm.saveToDiskLocked(); err != nil {
		rm.persistErr = err
		return err
	}
	return nil
}

func (rm *RevocationManager) IsNodeAllowed(nodeID string) bool {
	nodeID, err := NormalizeNodeID(nodeID)
	if err != nil {
		return false
	}
	rm.mu.RLock()
	defer rm.mu.RUnlock()
	if rm.loadErr != nil || rm.persistErr != nil {
		return false
	}
	if _, revoked := rm.revokedNodes[nodeID]; revoked {
		return false
	}
	return rm.allowedNodes[nodeID]
}

func NormalizeNodeID(raw string) (string, error) {
	nodeID := strings.TrimSpace(strings.ToLower(raw))
	if !validNodeID(nodeID) {
		return "", errors.New("invalid node ID")
	}
	return nodeID, nil
}

func validNodeID(nodeID string) bool {
	if nodeID == "" || len(nodeID) > 63 {
		return false
	}
	for i, r := range nodeID {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || (i > 0 && r == '-') {
			continue
		}
		return false
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
			Organization:       []string{"LabOps AI Infrastructure"},
			OrganizationalUnit: []string{"Security Root CA"},
			CommonName:         "LabOps Control Center Root CA (V2)",
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
	var err error
	nodeID, err = NormalizeNodeID(nodeID)
	if err != nil {
		return nil, errors.New("nodeID không hợp lệ")
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
			if validNodeID(nodeID) {
				return nodeID, nil
			}
		}
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

type tokenRecord struct {
	NodeID    string
	ExpiresAt int64
	Used      bool
}

// One-Time Enrollment Token gắn chặt với Node ID cụ thể
type EnrollmentTokenManager struct {
	mu     sync.Mutex
	secret []byte
	tokens map[string]*tokenRecord // token -> record
}

func NewEnrollmentTokenManager(secret string) *EnrollmentTokenManager {
	return &EnrollmentTokenManager{
		secret: []byte(secret),
		tokens: make(map[string]*tokenRecord),
	}
}

func (etm *EnrollmentTokenManager) GenerateToken(nodeID string, ttl time.Duration) string {
	var err error
	nodeID, err = NormalizeNodeID(nodeID)
	if len(etm.secret) < 32 || err != nil || ttl <= 0 || ttl > time.Hour {
		return ""
	}
	etm.mu.Lock()
	defer etm.mu.Unlock()

	randBytes := make([]byte, 16)
	if _, err := rand.Read(randBytes); err != nil {
		return ""
	}
	h := sha256.New()
	h.Write(etm.secret)
	h.Write([]byte(nodeID))
	h.Write(randBytes)
	token := hex.EncodeToString(h.Sum(nil))

	expiresAt := time.Now().Add(ttl).Unix()
	etm.tokens[token] = &tokenRecord{
		NodeID:    nodeID,
		ExpiresAt: expiresAt,
		Used:      false,
	}
	return token
}

// ConsumeTokenForNode xác minh token thuộc về đúng nodeID và chỉ được sử dụng một lần duy nhất
func (etm *EnrollmentTokenManager) ConsumeTokenForNode(token string, nodeID string) bool {
	var err error
	nodeID, err = NormalizeNodeID(nodeID)
	if err != nil {
		return false
	}
	etm.mu.Lock()
	defer etm.mu.Unlock()

	rec, exists := etm.tokens[token]
	if !exists || rec == nil || rec.Used {
		return false
	}
	if rec.NodeID != nodeID {
		// Phát hiện gian lận: token được cấp cho Node A nhưng mang sang ghi danh cho Node B
		return false
	}
	if time.Now().Unix() >= rec.ExpiresAt {
		delete(etm.tokens, token)
		return false
	}
	// Đánh dấu đã dùng và xóa token (chỉ sử dụng một lần)
	rec.Used = true
	delete(etm.tokens, token)
	return true
}

func (etm *EnrollmentTokenManager) ConsumeToken(token string) bool {
	etm.mu.Lock()
	defer etm.mu.Unlock()

	rec, exists := etm.tokens[token]
	if !exists || rec == nil || rec.Used {
		return false
	}
	if time.Now().Unix() >= rec.ExpiresAt {
		delete(etm.tokens, token)
		return false
	}
	rec.Used = true
	delete(etm.tokens, token)
	return true
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}
