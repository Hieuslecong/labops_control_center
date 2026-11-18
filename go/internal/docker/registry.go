package docker

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
)

var (
	ErrRegistryUnavailable = errors.New("REGISTRY_UNAVAILABLE: trusted workspace registry is not configured")
	ErrRegistryCorrupted   = errors.New("REGISTRY_CORRUPTED: trusted workspace registry file is unreadable or corrupted")
	canonicalCIDRegex      = regexp.MustCompile(`^[a-f0-9]{64}$`)
	imageDigestRegex       = regexp.MustCompile(`^(?:sha256:[a-f0-9]{64}|.+@sha256:[a-f0-9]{64})$`)
	safeWorkspaceNameRegex = regexp.MustCompile(`^[a-z0-9][a-z0-9_.-]{1,62}$`)
)

// IsCanonicalContainerID kiểm tra định danh Docker container SHA-256 64 ký tự hex chuẩn.
func IsCanonicalContainerID(id string) bool {
	return canonicalCIDRegex.MatchString(strings.TrimSpace(strings.ToLower(id)))
}

// IsImmutableImageRef kiểm tra image hoặc image digest phải được neo bất biến theo sha256.
func IsImmutableImageRef(image string) bool {
	return imageDigestRegex.MatchString(strings.TrimSpace(strings.ToLower(image)))
}

var forbiddenHostRoots = []string{
	"/etc", "/proc", "/sys", "/var/run", "/run", "/dev", "/boot", "/root",
	"/bin", "/sbin", "/usr", "/lib", "/lib64", "/var/lib/docker", "/mnt/HDD3",
}

// ValidateSafeStorageRoot kiểm tra thư mục storage của workspace không trỏ tới vùng hệ thống hoặc chứa symlink.
func ValidateSafeStorageRoot(storageRoot string) error {
	clean := filepath.Clean(strings.TrimSpace(storageRoot))
	if clean == "" || !filepath.IsAbs(clean) || clean == "/" || strings.Contains(storageRoot, "..") {
		return fmt.Errorf("INVALID_STORAGE_ROOT: đường dẫn storage '%s' không hợp lệ", storageRoot)
	}
	for _, pref := range forbiddenHostRoots {
		if clean == pref || strings.HasPrefix(clean, pref+"/") {
			return fmt.Errorf("FORBIDDEN_STORAGE_ROOT: đường dẫn '%s' nằm trong vùng hệ thống bị cấm (%s)", clean, pref)
		}
	}
	if clean == "/home" {
		return fmt.Errorf("FORBIDDEN_STORAGE_ROOT: cấm mount trực tiếp /home")
	}

	// Kiểm tra từng thành phần đang tồn tại không phải symlink (chống symlink traversal)
	cur := "/"
	parts := strings.Split(strings.TrimPrefix(clean, "/"), string(filepath.Separator))
	for _, part := range parts {
		if part == "" {
			continue
		}
		cur = filepath.Join(cur, part)
		info, err := os.Lstat(cur)
		if err != nil {
			if os.IsNotExist(err) {
				break
			}
			return fmt.Errorf("STORAGE_LSTAT_FAILED: %w", err)
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("SYMLINK_FORBIDDEN: đường dẫn storage chứa symlink tại '%s'", cur)
		}
	}
	return nil
}

// WorkspaceRecord là bản ghi nguồn sự thật cho từng Docker workspace trên node.
type WorkspaceRecord struct {
	WorkspaceID          string `json:"workspace_id"`
	Name                 string `json:"name"`
	MemberID             string `json:"member_id"`
	MemberUsername       string `json:"member_username"`
	UID                  int    `json:"uid"`
	GID                  int    `json:"gid"`
	NodeID               string `json:"node_id"`
	CanonicalContainerID string `json:"canonical_container_id"`
	ImageRef             string `json:"image_ref"`
	ImageDigest          string `json:"image_digest"`
	StorageRoot          string `json:"storage_root"`
	SSHPort              int    `json:"ssh_port"`
	VPNBindIP            string `json:"vpn_bind_ip"`
	CPULimitCores        int    `json:"cpu_limit_cores"`
	RAMLimitMB           int64  `json:"ram_limit_mb"`
	PidsLimit            int64  `json:"pids_limit"`
	Generation           uint64 `json:"generation"`
	DesiredState         string `json:"desired_state"`
	ObservedState        string `json:"observed_state"`
	LastVerifiedAt       string `json:"last_verified_at"`
	UpdatedAt            string `json:"updated_at"`
	AuditActor           string `json:"audit_actor"`
	CorrelationID        string `json:"correlation_id"`
}

type registryFileState struct {
	Records   map[string]*WorkspaceRecord `json:"records"`
	UpdatedAt string                      `json:"updated_at"`
}

// TrustedRegistry lưu trữ và xác minh ràng buộc giữa WorkspaceID, NodeID, Member và CanonicalContainerID.
type TrustedRegistry struct {
	mu        sync.RWMutex
	filePath  string
	corrupted bool
	loadErr   error
	records   map[string]*WorkspaceRecord
}

func NewTrustedRegistry(filePath string) (*TrustedRegistry, error) {
	reg := &TrustedRegistry{
		filePath: filePath,
		records:  make(map[string]*WorkspaceRecord),
	}
	if filePath != "" {
		if err := reg.load(); err != nil {
			reg.corrupted = true
			reg.loadErr = err
			return reg, err
		}
	}
	return reg, nil
}

func NewInMemoryRegistry() *TrustedRegistry {
	return &TrustedRegistry{
		records: make(map[string]*WorkspaceRecord),
	}
}

func (r *TrustedRegistry) load() error {
	info, err := os.Stat(r.filePath)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("%w: %v", ErrRegistryCorrupted, err)
	}
	if info.IsDir() {
		return fmt.Errorf("%w: path is a directory", ErrRegistryCorrupted)
	}
	raw, err := os.ReadFile(r.filePath)
	if err != nil {
		return fmt.Errorf("%w: %v", ErrRegistryCorrupted, err)
	}
	var st registryFileState
	if err := json.Unmarshal(raw, &st); err != nil {
		return fmt.Errorf("%w: %v", ErrRegistryCorrupted, err)
	}
	if st.Records != nil {
		r.records = st.Records
	}
	return nil
}

func (r *TrustedRegistry) saveLocked() error {
	if r.filePath == "" {
		return nil
	}
	dir := filepath.Dir(r.filePath)
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	st := registryFileState{
		Records:   r.records,
		UpdatedAt: time.Now().UTC().Format(time.RFC3339),
	}
	data, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		return err
	}
	randBuf := make([]byte, 4)
	_, _ = rand.Read(randBuf)
	tmpPath := fmt.Sprintf("%s.tmp.%s", r.filePath, hex.EncodeToString(randBuf))
	f, err := os.OpenFile(tmpPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0600)
	if err != nil {
		return err
	}
	if _, err := f.Write(data); err != nil {
		_ = f.Close()
		_ = os.Remove(tmpPath)
		return err
	}
	if err := f.Sync(); err != nil {
		_ = f.Close()
		_ = os.Remove(tmpPath)
		return err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(tmpPath)
		return err
	}
	return os.Rename(tmpPath, r.filePath)
}

func (r *TrustedRegistry) checkHealthyLocked() error {
	if r == nil {
		return ErrRegistryUnavailable
	}
	if r.corrupted {
		return fmt.Errorf("%w: %v", ErrRegistryCorrupted, r.loadErr)
	}
	return nil
}

func (r *TrustedRegistry) UpsertRecord(rec WorkspaceRecord) error {
	if strings.TrimSpace(rec.WorkspaceID) == "" || strings.TrimSpace(rec.Name) == "" {
		return errors.New("workspace_id and name are required")
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if err := r.checkHealthyLocked(); err != nil {
		return err
	}
	rec.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	cp := rec
	r.records[rec.WorkspaceID] = &cp
	return r.saveLocked()
}

func (r *TrustedRegistry) GetByWorkspaceID(workspaceID string) (*WorkspaceRecord, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if err := r.checkHealthyLocked(); err != nil {
		return nil, err
	}
	rec, ok := r.records[workspaceID]
	if !ok || rec == nil {
		return nil, fmt.Errorf("UNKNOWN_WORKSPACE: không tìm thấy workspace '%s' trong registry", workspaceID)
	}
	cp := *rec
	return &cp, nil
}

func (r *TrustedRegistry) GetByContainerID(canonicalID string) (*WorkspaceRecord, error) {
	canonicalID = strings.TrimSpace(strings.ToLower(canonicalID))
	r.mu.RLock()
	defer r.mu.RUnlock()
	if err := r.checkHealthyLocked(); err != nil {
		return nil, err
	}
	for _, rec := range r.records {
		if strings.EqualFold(rec.CanonicalContainerID, canonicalID) {
			cp := *rec
			return &cp, nil
		}
	}
	return nil, fmt.Errorf("UNKNOWN_CONTAINER: container '%s' không tồn tại trong trusted registry", canonicalID)
}

// VerifyContainerBinding xác minh container thực tế khớp 100% metadata bất biến trong TrustedRegistry.
func (r *TrustedRegistry) VerifyContainerBinding(nodeID, workspaceID string, live *dockerInspectJSON) (*WorkspaceRecord, error) {
	if r == nil {
		return nil, ErrRegistryUnavailable
	}
	if live == nil {
		return nil, errors.New("CONTAINER_NOT_FOUND: không có dữ liệu Docker inspect")
	}
	liveID := strings.TrimSpace(strings.ToLower(live.ID))
	if !IsCanonicalContainerID(liveID) {
		return nil, fmt.Errorf("INVALID_CANONICAL_ID: container ID '%s' không phải chuẩn 64-hex", live.ID)
	}

	r.mu.RLock()
	defer r.mu.RUnlock()
	if err := r.checkHealthyLocked(); err != nil {
		return nil, err
	}

	var rec *WorkspaceRecord
	if workspaceID != "" {
		rec = r.records[workspaceID]
	} else {
		lblWS := strings.TrimSpace(live.Config.Labels["labops.workspace_id"])
		if lblWS != "" {
			rec = r.records[lblWS]
		}
	}
	if rec == nil {
		return nil, fmt.Errorf("UNTRUSTED_CONTAINER: container '%s' không thuộc bất kỳ bản ghi hợp lệ nào trong registry", liveID)
	}

	if rec.NodeID != nodeID {
		return nil, fmt.Errorf("NODE_MISMATCH: registry node '%s' != host node '%s'", rec.NodeID, nodeID)
	}
	if rec.CanonicalContainerID == "" || !strings.EqualFold(rec.CanonicalContainerID, liveID) {
		return nil, fmt.Errorf("CONTAINER_REPLACED_OR_MISMATCH: expected ID '%s', got '%s'", rec.CanonicalContainerID, liveID)
	}
	liveName := strings.TrimPrefix(live.Name, "/")
	if rec.Name != "" && liveName != "" && liveName != rec.Name {
		return nil, fmt.Errorf("CONTAINER_NAME_MISMATCH: expected '%s', got '%s'", rec.Name, liveName)
	}

	// Không bao giờ tin label đơn lẻ — toàn bộ labels phải khớp chính xác bản ghi registry
	labels := live.Config.Labels
	if labels["labops.managed"] != "true" {
		return nil, errors.New("UNMANAGED_CONTAINER: thiếu label labops.managed=true")
	}
	if labels["labops.workspace_id"] != rec.WorkspaceID {
		return nil, fmt.Errorf("LABEL_SPOOF_WORKSPACE: expected '%s', got '%s'", rec.WorkspaceID, labels["labops.workspace_id"])
	}
	if labels["labops.member_id"] != rec.MemberID || labels["labops.member"] != rec.MemberUsername {
		return nil, fmt.Errorf("OWNER_MISMATCH: label member không khớp bản ghi registry (%s / %s)", rec.MemberID, rec.MemberUsername)
	}
	if labels["labops.node_id"] != rec.NodeID {
		return nil, fmt.Errorf("LABEL_NODE_MISMATCH: expected '%s', got '%s'", rec.NodeID, labels["labops.node_id"])
	}
	if labels["labops.generation"] != strconv.FormatUint(rec.Generation, 10) {
		return nil, fmt.Errorf("GENERATION_MISMATCH: expected %d, got '%s'", rec.Generation, labels["labops.generation"])
	}
	if rec.ImageDigest != "" && live.Image != "" && !strings.EqualFold(live.Image, rec.ImageDigest) {
		return nil, fmt.Errorf("IMAGE_DIGEST_MISMATCH: expected '%s', got '%s'", rec.ImageDigest, live.Image)
	}

	cp := *rec
	return &cp, nil
}
