package repositoryguard

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type CacheEntry struct {
	CanonicalRepo  string    `json:"canonical_repo"`
	CommitSHA      string    `json:"commit_sha"`
	ArtifactDigest string    `json:"artifact_digest"`
	ArtifactPath   string    `json:"artifact_path"`
	ArtifactSize   int64     `json:"artifact_size"`
	PolicyVersion  string    `json:"policy_version"`
	ScannerVersion string    `json:"scanner_version"`
	DBVersion      string    `json:"db_version"`
	ApprovedAt     time.Time `json:"approved_at"`
	ApprovedBy     string    `json:"approved_by"` // "BOT_AUTO_APPROVAL" hoặc admin username
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
		if os.IsNotExist(err) {
			_ = s.saveIndexLocked()
		} else {
			return nil, fmt.Errorf("approved store index bị hỏng (fail-closed): %w", err)
		}
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
		if entry != nil {
			s.entries[s.cacheKey(entry.CanonicalRepo, entry.CommitSHA)] = entry
		}
	}
	return nil
}

func (s *ApprovedArtifactStore) saveIndexLocked() error {
	list := make([]*CacheEntry, 0, len(s.entries))
	for _, entry := range s.entries {
		list = append(list, entry)
	}

	data, err := json.MarshalIndent(list, "", "  ")
	if err != nil {
		return err
	}

	tmp, err := os.CreateTemp(s.storeDir, "index.json.tmp.*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	if err := tmp.Chmod(0600); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmpName)
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmpName)
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmpName)
		return err
	}
	if err := tmp.Close(); err != nil {
		_ = os.Remove(tmpName)
		return err
	}
	return os.Rename(tmpName, s.indexPath)
}

// GetApproved kiểm tra commit đã duyệt, đúng PolicyVersion hiện tại và có artifact thực sự hợp lệ.
func (s *ApprovedArtifactStore) GetApproved(canonicalRepo, commitSHA string) (*CacheEntry, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	key := s.cacheKey(canonicalRepo, commitSHA)
	entry, ok := s.entries[key]
	if !ok || entry == nil {
		return nil, false
	}
	if entry.PolicyVersion != CurrentPolicyVersion {
		return nil, false
	}
	if strings.TrimSpace(entry.ArtifactDigest) == "" {
		return nil, false
	}

	info, err := os.Stat(entry.ArtifactPath)
	if err != nil || info.IsDir() {
		return nil, false
	}

	cp := *entry
	return &cp, true
}

// RevokeEntry xóa commit khỏi approved store khi bị thu hồi.
func (s *ApprovedArtifactStore) RevokeEntry(canonicalRepo, commitSHA string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	key := s.cacheKey(canonicalRepo, commitSHA)
	if ent, ok := s.entries[key]; ok && ent != nil {
		if ent.ArtifactPath != "" {
			_ = os.Chmod(ent.ArtifactPath, 0600)
			_ = os.Remove(ent.ArtifactPath)
		}
		delete(s.entries, key)
		return s.saveIndexLocked()
	}
	return nil
}

// StoreApproved đối chiếu SHA-256 trước khi promote vào Approved Store bất biến (temp + fsync + chmod 0440 + rename).
func (s *ApprovedArtifactStore) StoreApproved(canonicalRepo, commitSHA, scannerVer, dbVer, approvedBy string, qw *QuarantineWorkspace) (*CacheEntry, error) {
	if qw == nil || strings.TrimSpace(qw.ArchiveFile) == "" || strings.TrimSpace(qw.DigestSHA256) == "" {
		return nil, errors.New("thiếu archive quarantine hoặc digest SHA-256")
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	src, err := os.Open(qw.ArchiveFile)
	if err != nil {
		return nil, fmt.Errorf("không thể đọc file archive từ quarantine: %w", err)
	}
	defer src.Close()

	tmpFile, err := os.CreateTemp(s.storeDir, "artifact.tmp.*")
	if err != nil {
		return nil, fmt.Errorf("không thể tạo tệp tạm artifact: %w", err)
	}
	tmpPath := tmpFile.Name()

	hasher := sha256.New()
	size, copyErr := io.Copy(io.MultiWriter(tmpFile, hasher), io.LimitReader(src, MaxRepoSizeBytes+1))
	if copyErr != nil {
		_ = tmpFile.Close()
		_ = os.Remove(tmpPath)
		return nil, fmt.Errorf("lỗi copy artifact: %w", copyErr)
	}
	if size > MaxRepoSizeBytes {
		_ = tmpFile.Close()
		_ = os.Remove(tmpPath)
		return nil, errors.New("kích thước artifact vượt giới hạn tối đa")
	}

	actualDigest := hex.EncodeToString(hasher.Sum(nil))
	if !strings.EqualFold(actualDigest, qw.DigestSHA256) {
		_ = tmpFile.Close()
		_ = os.Remove(tmpPath)
		return nil, fmt.Errorf("TOCTOU_QUARANTINE_MISMATCH: digest quarantine bị thay đổi trước khi phê duyệt (expected %s, got %s)", qw.DigestSHA256, actualDigest)
	}

	if err := tmpFile.Chmod(0440); err != nil {
		_ = tmpFile.Close()
		_ = os.Remove(tmpPath)
		return nil, err
	}
	if err := tmpFile.Sync(); err != nil {
		_ = tmpFile.Close()
		_ = os.Remove(tmpPath)
		return nil, err
	}
	if err := tmpFile.Close(); err != nil {
		_ = os.Remove(tmpPath)
		return nil, err
	}

	targetFilename := fmt.Sprintf("%s.tar.gz", actualDigest)
	targetPath := filepath.Join(s.storeDir, targetFilename)
	if existingInfo, statErr := os.Stat(targetPath); statErr == nil && !existingInfo.IsDir() {
		// Artifact content-addressed cùng digest đã tồn tại ở chế độ read-only -> giữ nguyên
		_ = os.Remove(tmpPath)
	} else if err := os.Rename(tmpPath, targetPath); err != nil {
		_ = os.Remove(tmpPath)
		return nil, fmt.Errorf("không thể niêm phong artifact vào approved store: %w", err)
	}

	entry := &CacheEntry{
		CanonicalRepo:  canonicalRepo,
		CommitSHA:      commitSHA,
		ArtifactDigest: actualDigest,
		ArtifactPath:   targetPath,
		ArtifactSize:   size,
		PolicyVersion:  CurrentPolicyVersion,
		ScannerVersion: scannerVer,
		DBVersion:      dbVer,
		ApprovedAt:     time.Now().UTC(),
		ApprovedBy:     approvedBy,
	}

	s.entries[s.cacheKey(canonicalRepo, commitSHA)] = entry
	if err := s.saveIndexLocked(); err != nil {
		return nil, fmt.Errorf("lỗi lưu index approved store: %w", err)
	}

	cp := *entry
	return &cp, nil
}
