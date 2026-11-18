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
	"math/big"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
)

const (
	NodeSANPrefix     = ".node.labops.local"
	DefaultCAValidity = 10 * 365 * 24 * time.Hour
	DefaultCertValid  = 365 * 24 * time.Hour
)

var validNodeIDPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{1,62}$`)

// IsValidNodeID kiểm tra định danh Node ID chuẩn an toàn.
func IsValidNodeID(nodeID string) bool {
	return validNodeIDPattern.MatchString(strings.TrimSpace(strings.ToLower(nodeID)))
}

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

// PKIAuditEntry ghi lại các sự kiện cấp/thu hồi chứng chỉ hoặc un-revoke node.
type PKIAuditEntry struct {
	Timestamp   string `json:"timestamp"`
	Action      string `json:"action"`
	NodeID      string `json:"node_id"`
	SerialHex   string `json:"serial_hex,omitempty"`
	Fingerprint string `json:"fingerprint_sha256,omitempty"`
	Actor       string `json:"actor,omitempty"`
	Reason      string `json:"reason,omitempty"`
}

// RevocationData cấu trúc lưu trữ danh sách thu hồi trên đĩa
type RevocationData struct {
	RevokedNodes        map[string]string `json:"revoked_nodes"`
	AllowedNodes        map[string]bool   `json:"allowed_nodes"`
	RevokedSerials      map[string]string `json:"revoked_serials,omitempty"`
	RevokedFingerprints map[string]string `json:"revoked_fingerprints,omitempty"`
	AuditLog            []PKIAuditEntry   `json:"audit_log,omitempty"`
	UpdatedAt           string            `json:"updated_at"`
}

// RevocationManager quản lý danh sách thu hồi node certificate (fail-closed, atomic persistence).
type RevocationManager struct {
	mu                  sync.RWMutex
	filePath            string
	corrupted           bool
	loadErr             error
	revokedNodes        map[string]string // node_id -> reason
	allowedNodes        map[string]bool   // node_id -> allowed
	revokedSerials      map[string]string // serial hex lowercase -> reason
	revokedFingerprints map[string]string // sha256 cert DER hex -> reason
	auditLog            []PKIAuditEntry
}

func NewRevocationManager() *RevocationManager {
	return NewRevocationManagerWithPersistence("")
}

func NewRevocationManagerWithPersistence(filePath string) *RevocationManager {
	rm := &RevocationManager{
		filePath:            filePath,
		revokedNodes:        make(map[string]string),
		allowedNodes:        make(map[string]bool),
		revokedSerials:      make(map[string]string),
		revokedFingerprints: make(map[string]string),
		auditLog:            make([]PKIAuditEntry, 0),
	}
	if filePath != "" {
		rm.loadFromDisk()
	}
	return rm
}

func (rm *RevocationManager) loadFromDisk() {
	if rm.filePath == "" || !fileExists(rm.filePath) {
		return
	}
	data, err := os.ReadFile(rm.filePath)
	if err != nil {
		rm.corrupted = true
		rm.loadErr = fmt.Errorf("không thể đọc PKI state %s: %w", rm.filePath, err)
		return
	}
	var rd RevocationData
	if err := json.Unmarshal(data, &rd); err != nil {
		rm.corrupted = true
		rm.loadErr = fmt.Errorf("PKI state %s bị hỏng JSON (fail-closed): %w", rm.filePath, err)
		return
	}
	if rd.RevokedNodes != nil {
		for k, v := range rd.RevokedNodes {
			rm.revokedNodes[strings.ToLower(strings.TrimSpace(k))] = v
		}
	}
	if rd.AllowedNodes != nil {
		for k, v := range rd.AllowedNodes {
			rm.allowedNodes[strings.ToLower(strings.TrimSpace(k))] = v
		}
	}
	if rd.RevokedSerials != nil {
		for k, v := range rd.RevokedSerials {
			rm.revokedSerials[strings.ToLower(strings.TrimSpace(k))] = v
		}
	}
	if rd.RevokedFingerprints != nil {
		for k, v := range rd.RevokedFingerprints {
			rm.revokedFingerprints[strings.ToLower(strings.TrimSpace(k))] = v
		}
	}
	if rd.AuditLog != nil {
		rm.auditLog = rd.AuditLog
	}
}

func atomicWriteFile(path string, data []byte, perm os.FileMode) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, filepath.Base(path)+".tmp.*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	cleanup := func() {
		_ = tmp.Close()
		_ = os.Remove(tmpName)
	}
	if err := tmp.Chmod(perm); err != nil {
		cleanup()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		cleanup()
		return err
	}
	if err := tmp.Sync(); err != nil {
		cleanup()
		return err
	}
	if err := tmp.Close(); err != nil {
		_ = os.Remove(tmpName)
		return err
	}
	if err := os.Rename(tmpName, path); err != nil {
		_ = os.Remove(tmpName)
		return err
	}
	if dfd, err := os.Open(dir); err == nil {
		_ = dfd.Sync()
		_ = dfd.Close()
	}
	return nil
}

func (rm *RevocationManager) appendAuditLocked(action, nodeID, serialHex, fingerprint, actor, reason string) {
	rm.auditLog = append(rm.auditLog, PKIAuditEntry{
		Timestamp:   time.Now().UTC().Format(time.RFC3339),
		Action:      action,
		NodeID:      nodeID,
		SerialHex:   serialHex,
		Fingerprint: fingerprint,
		Actor:       actor,
		Reason:      reason,
	})
	if len(rm.auditLog) > 1000 {
		rm.auditLog = rm.auditLog[len(rm.auditLog)-1000:]
	}
}

func (rm *RevocationManager) saveToDiskLocked() error {
	if rm.filePath == "" {
		return nil
	}
	rd := RevocationData{
		RevokedNodes:        rm.revokedNodes,
		AllowedNodes:        rm.allowedNodes,
		RevokedSerials:      rm.revokedSerials,
		RevokedFingerprints: rm.revokedFingerprints,
		AuditLog:            rm.auditLog,
		UpdatedAt:           time.Now().UTC().Format(time.RFC3339),
	}
	data, err := json.MarshalIndent(rd, "", "  ")
	if err != nil {
		return err
	}
	return atomicWriteFile(rm.filePath, data, 0600)
}

func (rm *RevocationManager) IsCorrupted() bool {
	rm.mu.RLock()
	defer rm.mu.RUnlock()
	return rm.corrupted
}

func (rm *RevocationManager) AllowNode(nodeID string) error {
	nodeID = strings.TrimSpace(strings.ToLower(nodeID))
	if !IsValidNodeID(nodeID) {
		return fmt.Errorf("nodeID '%s' không hợp lệ", nodeID)
	}
	rm.mu.Lock()
	defer rm.mu.Unlock()
	if rm.corrupted {
		return fmt.Errorf("từ chối cập nhật PKI: trạng thái revocation bị hỏng (%v)", rm.loadErr)
	}
	if reason, revoked := rm.revokedNodes[nodeID]; revoked {
		return fmt.Errorf("từ chối tự động gỡ thu hồi node '%s' (đang bị thu hồi: %s); phải gọi UnrevokeNode tường minh", nodeID, reason)
	}
	rm.allowedNodes[nodeID] = true
	rm.appendAuditLocked("ALLOW_NODE", nodeID, "", "", "control-plane", "Node added to allowlist")
	return rm.saveToDiskLocked()
}

func (rm *RevocationManager) UnrevokeNode(nodeID, actor, reason string) error {
	nodeID = strings.TrimSpace(strings.ToLower(nodeID))
	if !IsValidNodeID(nodeID) {
		return fmt.Errorf("nodeID '%s' không hợp lệ", nodeID)
	}
	if strings.TrimSpace(actor) == "" {
		return errors.New("bắt buộc phải ghi rõ actor khi un-revoke node")
	}
	rm.mu.Lock()
	defer rm.mu.Unlock()
	if rm.corrupted {
		return fmt.Errorf("từ chối cập nhật PKI: trạng thái revocation bị hỏng (%v)", rm.loadErr)
	}
	delete(rm.revokedNodes, nodeID)
	rm.allowedNodes[nodeID] = true
	rm.appendAuditLocked("UNREVOKE_NODE", nodeID, "", "", actor, reason)
	return rm.saveToDiskLocked()
}

func (rm *RevocationManager) RevokeNode(nodeID string, reason string) error {
	nodeID = strings.TrimSpace(strings.ToLower(nodeID))
	if nodeID == "" {
		return errors.New("nodeID không được rỗng")
	}
	rm.mu.Lock()
	defer rm.mu.Unlock()
	if rm.corrupted {
		return fmt.Errorf("từ chối cập nhật PKI: trạng thái revocation bị hỏng (%v)", rm.loadErr)
	}
	delete(rm.allowedNodes, nodeID)
	rm.revokedNodes[nodeID] = reason
	rm.appendAuditLocked("REVOKE_NODE", nodeID, "", "", "control-plane", reason)
	return rm.saveToDiskLocked()
}

func (rm *RevocationManager) RevokeCertificate(nodeID, serialHex, fingerprintSHA256, reason string) error {
	nodeID = strings.TrimSpace(strings.ToLower(nodeID))
	serialHex = strings.TrimSpace(strings.ToLower(serialHex))
	fingerprintSHA256 = strings.TrimSpace(strings.ToLower(fingerprintSHA256))
	if serialHex == "" && fingerprintSHA256 == "" {
		return errors.New("cần serialHex hoặc fingerprintSHA256 để thu hồi chứng chỉ")
	}
	rm.mu.Lock()
	defer rm.mu.Unlock()
	if rm.corrupted {
		return fmt.Errorf("từ chối cập nhật PKI: trạng thái revocation bị hỏng (%v)", rm.loadErr)
	}
	if serialHex != "" {
		rm.revokedSerials[serialHex] = reason
	}
	if fingerprintSHA256 != "" {
		rm.revokedFingerprints[fingerprintSHA256] = reason
	}
	rm.appendAuditLocked("REVOKE_CERT", nodeID, serialHex, fingerprintSHA256, "control-plane", reason)
	return rm.saveToDiskLocked()
}

func (rm *RevocationManager) RecordCertIssued(nodeID string, cert *x509.Certificate, actor string) error {
	if cert == nil {
		return errors.New("cert rỗng")
	}
	nodeID = strings.TrimSpace(strings.ToLower(nodeID))
	serialHex := strings.ToLower(cert.SerialNumber.Text(16))
	fp := CertFingerprintSHA256(cert)
	rm.mu.Lock()
	defer rm.mu.Unlock()
	if rm.corrupted {
		return fmt.Errorf("từ chối ghi audit PKI: %v", rm.loadErr)
	}
	rm.appendAuditLocked("ISSUE_CERT", nodeID, serialHex, fp, actor, "Node client certificate issued")
	return rm.saveToDiskLocked()
}

func (rm *RevocationManager) IsNodeRevoked(nodeID string) bool {
	nodeID = strings.TrimSpace(strings.ToLower(nodeID))
	rm.mu.RLock()
	defer rm.mu.RUnlock()
	_, revoked := rm.revokedNodes[nodeID]
	return revoked
}

func (rm *RevocationManager) CanIssueForNode(nodeID string) error {
	nodeID = strings.TrimSpace(strings.ToLower(nodeID))
	if !IsValidNodeID(nodeID) {
		return fmt.Errorf("nodeID '%s' không hợp lệ", nodeID)
	}
	rm.mu.RLock()
	defer rm.mu.RUnlock()
	if rm.corrupted {
		return fmt.Errorf("PKI revocation store bị hỏng: %v", rm.loadErr)
	}
	if reason, revoked := rm.revokedNodes[nodeID]; revoked {
		return fmt.Errorf("node '%s' đã bị thu hồi (%s); không thể cấp chứng chỉ", nodeID, reason)
	}
	if len(rm.allowedNodes) == 0 || !rm.allowedNodes[nodeID] {
		return fmt.Errorf("node '%s' chưa nằm trong allowlist được Admin phê duyệt", nodeID)
	}
	return nil
}

func (rm *RevocationManager) AuditEntries() []PKIAuditEntry {
	rm.mu.RLock()
	defer rm.mu.RUnlock()
	out := make([]PKIAuditEntry, len(rm.auditLog))
	copy(out, rm.auditLog)
	return out
}

// IsNodeAllowed từ chối mặc định (deny-by-default) nếu allowlist rỗng hoặc dữ liệu hỏng.
func (rm *RevocationManager) IsNodeAllowed(nodeID string) bool {
	nodeID = strings.TrimSpace(strings.ToLower(nodeID))
	if nodeID == "" {
		return false
	}
	rm.mu.RLock()
	defer rm.mu.RUnlock()
	if rm.corrupted {
		return false
	}
	if _, revoked := rm.revokedNodes[nodeID]; revoked {
		return false
	}
	if len(rm.allowedNodes) == 0 {
		return false
	}
	return rm.allowedNodes[nodeID]
}

// CertFingerprintSHA256 tính toán fingerprint SHA-256 dạng hex của certificate DER.
func CertFingerprintSHA256(cert *x509.Certificate) string {
	if cert == nil {
		return ""
	}
	sum := sha256.Sum256(cert.Raw)
	return hex.EncodeToString(sum[:])
}

// IsCertAllowed kiểm tra đồng thời node ID allowlist + serial/fingerprint revocation.
func (rm *RevocationManager) IsCertAllowed(cert *x509.Certificate) error {
	if rm == nil {
		return errors.New("mTLS: thiếu RevocationManager (fail-closed)")
	}
	nodeID, err := ExtractNodeIDFromCert(cert)
	if err != nil {
		return fmt.Errorf("mTLS: %w", err)
	}
	serialHex := ""
	if cert.SerialNumber != nil {
		serialHex = strings.ToLower(cert.SerialNumber.Text(16))
	}
	fp := CertFingerprintSHA256(cert)

	rm.mu.RLock()
	defer rm.mu.RUnlock()
	if rm.corrupted {
		return fmt.Errorf("mTLS: PKI state bị hỏng (%v)", rm.loadErr)
	}
	if reason, revoked := rm.revokedNodes[nodeID]; revoked {
		return fmt.Errorf("mTLS: node '%s' đã bị thu hồi (%s)", nodeID, reason)
	}
	if serialHex != "" {
		if reason, revoked := rm.revokedSerials[serialHex]; revoked {
			return fmt.Errorf("mTLS: chứng chỉ serial %s đã bị thu hồi (%s)", serialHex, reason)
		}
	}
	if fp != "" {
		if reason, revoked := rm.revokedFingerprints[fp]; revoked {
			return fmt.Errorf("mTLS: chứng chỉ fingerprint đã bị thu hồi (%s)", reason)
		}
	}
	if len(rm.allowedNodes) == 0 || !rm.allowedNodes[nodeID] {
		return fmt.Errorf("mTLS: node '%s' không nằm trong allowlist", nodeID)
	}
	return nil
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

	if err := atomicWriteFile(certPath, certPEM, 0644); err != nil {
		return nil, fmt.Errorf("không thể lưu ca.crt: %w", err)
	}
	if err := atomicWriteFile(keyPath, keyPEM, 0600); err != nil {
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
	if !IsValidNodeID(nodeID) {
		return nil, fmt.Errorf("nodeID '%s' không hợp lệ (chỉ cho phép [a-z0-9_-], dài 2-63 ký tự)", nodeID)
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

// ExtractNodeIDFromCert trích xuất và đối chiếu chặt chẽ Node ID giữa SAN và CommonName.
func ExtractNodeIDFromCert(cert *x509.Certificate) (string, error) {
	if cert == nil {
		return "", errors.New("chứng chỉ rỗng")
	}

	var matched []string
	for _, dns := range cert.DNSNames {
		dnsLower := strings.TrimSpace(strings.ToLower(dns))
		if strings.HasSuffix(dnsLower, NodeSANPrefix) {
			candidate := strings.TrimSuffix(dnsLower, NodeSANPrefix)
			if !IsValidNodeID(candidate) {
				return "", fmt.Errorf("SAN '%s' chứa node ID không hợp lệ", dns)
			}
			matched = append(matched, candidate)
		}
	}

	if len(matched) == 0 {
		return "", errors.New("chứng chỉ client không chứa SAN <node_id>.node.labops.local hợp lệ")
	}
	if len(matched) > 1 {
		for _, m := range matched[1:] {
			if m != matched[0] {
				return "", errors.New("chứng chỉ client chứa nhiều SAN node ID xung đột")
			}
		}
	}

	nodeID := matched[0]
	cn := strings.TrimSpace(strings.ToLower(cert.Subject.CommonName))
	if cn == "" || cn != nodeID {
		return "", fmt.Errorf("chứng chỉ có CommonName '%s' không khớp với SAN node ID '%s'", cert.Subject.CommonName, nodeID)
	}

	return nodeID, nil
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
			if rm == nil {
				return errors.New("mTLS: thiếu RevocationManager (fail-closed)")
			}
			clientCert := verifiedChains[0][0]
			return rm.IsCertAllowed(clientCert)
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
	trimmed := strings.TrimSpace(secret)
	var secretBytes []byte
	if trimmed != "" {
		secretBytes = []byte(trimmed)
	}
	return &EnrollmentTokenManager{
		secret: secretBytes,
		tokens: make(map[string]*tokenRecord),
	}
}

func (etm *EnrollmentTokenManager) GenerateToken(nodeID string, ttl time.Duration) string {
	nodeID = strings.TrimSpace(strings.ToLower(nodeID))
	if len(etm.secret) == 0 || !IsValidNodeID(nodeID) {
		return ""
	}
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
	etm.tokens[token] = &tokenRecord{
		NodeID:    nodeID,
		ExpiresAt: expiresAt,
		Used:      false,
	}
	return token
}

// ValidateTokenForNode kiểm tra không tiêu thụ xem token còn hợp lệ và khớp nodeID hay không.
func (etm *EnrollmentTokenManager) ValidateTokenForNode(token string, nodeID string) bool {
	if len(etm.secret) == 0 || strings.TrimSpace(token) == "" {
		return false
	}
	nodeID = strings.TrimSpace(strings.ToLower(nodeID))
	etm.mu.Lock()
	defer etm.mu.Unlock()

	rec, exists := etm.tokens[token]
	if !exists || rec == nil || rec.Used {
		return false
	}
	if rec.NodeID != nodeID {
		return false
	}
	if time.Now().Unix() > rec.ExpiresAt {
		delete(etm.tokens, token)
		return false
	}
	return true
}

// ConsumeTokenForNode xác minh token thuộc về đúng nodeID và chỉ được sử dụng một lần duy nhất
func (etm *EnrollmentTokenManager) ConsumeTokenForNode(token string, nodeID string) bool {
	if len(etm.secret) == 0 || strings.TrimSpace(token) == "" {
		return false
	}
	nodeID = strings.TrimSpace(strings.ToLower(nodeID))
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
	if time.Now().Unix() > rec.ExpiresAt {
		delete(etm.tokens, token)
		return false
	}
	// Đánh dấu đã dùng và xóa token (chỉ sử dụng một lần)
	rec.Used = true
	delete(etm.tokens, token)
	return true
}

func (etm *EnrollmentTokenManager) ConsumeToken(token string) bool {
	if len(etm.secret) == 0 || strings.TrimSpace(token) == "" {
		return false
	}
	etm.mu.Lock()
	defer etm.mu.Unlock()

	rec, exists := etm.tokens[token]
	if !exists || rec == nil || rec.Used {
		return false
	}
	if time.Now().Unix() > rec.ExpiresAt {
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

