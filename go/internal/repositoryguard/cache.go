package repositoryguard

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sync"
	"time"
)

type CacheEntry struct {
	CanonicalRepo   string    `json:"canonical_repo"`
	CommitSHA       string    `json:"commit_sha"`
	ArtifactDigest  string    `json:"artifact_digest"`
	ArtifactPath    string    `json:"artifact_path"`
	ArtifactSize    int64     `json:"artifact_size"`
	PolicyVersion   string    `json:"policy_version"`
	ScannerVersion  string    `json:"scanner_version"`
	DBVersion       string    `json:"db_version"`
	ApprovedAt      time.Time `json:"approved_at"`
	ApprovedBy      string    `json:"approved_by"` // "BOT_AUTO_APPROVAL" hoặc admin username
}

type ApprovedArtifactStore struct {
	storeDir  string
	indexPath string
	mu        sync.RWMutex
	entries   map[string]*CacheEntry // key: canonicalRepo + "|" + commitSHA
}

func NewApprovedArtifactStore(storeDir string) (*ApprovedArtifactStore, error) {
	if err := os.MkdirAll(storeDir, 0750); err != nil {
		return nil, fmt.Errorf("không thể tạo thư mục approved store: %w", err)
	}

	s := &ApprovedArtifactStore{
		storeDir:  storeDir,
		indexPath: filepath.Join(storeDir, "index.json"),
		entries:   make(map[string]*CacheEntry),
	}

	if err := s.loadIndex(); err != nil {
		// Khởi tạo index trống nếu chưa có
		_ = s.saveIndex()
	}

	return s, nil
}

func (s *ApprovedArtifactStore) cacheKey(canonicalRepo, commitSHA string) string {
	return fmt.Sprintf("%s|%s", canonicalRepo, commitSHA)
}

func (s *ApprovedArtifactStore) loadIndex() error {
	s.mu.Lock()
	defer s.mu.Unlock()

	data, err := os.ReadFile(s.indexPath)
	if err != nil {
		return err
	}

	var list []*CacheEntry
	if err := json.Unmarshal(data, &list); err != nil {
		return err
	}

	s.entries = make(map[string]*CacheEntry)
	for _, entry := range list {
		s.entries[s.cacheKey(entry.CanonicalRepo, entry.CommitSHA)] = entry
	}
	return nil
}

func (s *ApprovedArtifactStore) saveIndex() error {
	var list []*CacheEntry
	for _, entry := range s.entries {
		list = append(list, entry)
	}

	data, err := json.MarshalIndent(list, "", "  ")
	if err != nil {
		return err
	}

	tmp := s.indexPath + ".tmp"
	if err := os.WriteFile(tmp, data, 0640); err != nil {
		return err
	}
	return os.Rename(tmp, s.indexPath)
}

// GetApproved kiểm tra xem commit này đã được duyệt và có artifact hợp lệ hay không
func (s *ApprovedArtifactStore) GetApproved(canonicalRepo, commitSHA string) (*CacheEntry, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	key := s.cacheKey(canonicalRepo, commitSHA)
	entry, ok := s.entries[key]
	if !ok {
		return nil, false
	}

	// Xác minh tệp artifact thực tế trên đĩa
	if _, err := os.Stat(entry.ArtifactPath); err != nil {
		return nil, false
	}

	return entry, true
}

// StoreApproved sao chép gói archive đã quét vào Approved Artifact Store
func (s *ApprovedArtifactStore) StoreApproved(canonicalRepo, commitSHA, scannerVer, dbVer, approvedBy string, qw *QuarantineWorkspace) (*CacheEntry, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	// Lưu tệp content-addressed theo SHA-256 digest
	targetFilename := fmt.Sprintf("%s.tar.gz", qw.DigestSHA256)
	targetPath := filepath.Join(s.storeDir, targetFilename)

	// Copy archive vào kho lưu trữ được bảo vệ
	src, err := os.Open(qw.ArchiveFile)
	if err != nil {
		return nil, fmt.Errorf("không thể đọc file archive từ quarantine: %w", err)
	}
	defer src.Close()

	dst, err := os.OpenFile(targetPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0640)
	if err != nil {
		return nil, fmt.Errorf("không thể tạo file artifact trong store: %w", err)
	}
	defer dst.Close()

	size, err := io.Copy(dst, src)
	if err != nil {
		return nil, fmt.Errorf("lỗi copy artifact: %w", err)
	}

	entry := &CacheEntry{
		CanonicalRepo:  canonicalRepo,
		CommitSHA:      commitSHA,
		ArtifactDigest: qw.DigestSHA256,
		ArtifactPath:   targetPath,
		ArtifactSize:   size,
		PolicyVersion:  CurrentPolicyVersion,
		ScannerVersion: scannerVer,
		DBVersion:      dbVer,
		ApprovedAt:     time.Now(),
		ApprovedBy:     approvedBy,
	}

	s.entries[s.cacheKey(canonicalRepo, commitSHA)] = entry
	if err := s.saveIndex(); err != nil {
		return nil, fmt.Errorf("lỗi lưu index approved store: %w", err)
	}

	return entry, nil
}
