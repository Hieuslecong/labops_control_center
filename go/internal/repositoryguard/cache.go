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

	"golang.org/x/sys/unix"
)

type CacheEntry struct {
	CanonicalRepo  string    `json:"canonical_repo"`
	CommitSHA      string    `json:"commit_sha"`
	ArtifactDigest string    `json:"artifact_digest"`
	ArtifactPath   string    `json:"artifact_path"`
	ArtifactSize   int64     `json:"artifact_size"`
	BundleDigest   string    `json:"bundle_digest,omitempty"`
	BundlePath     string    `json:"bundle_path,omitempty"`
	BundleSize     int64     `json:"bundle_size,omitempty"`
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
	abs, err := filepath.Abs(storeDir)
	if err != nil {
		return nil, err
	}
	info, err := os.Lstat(abs)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("approved store must be a real directory")
	}
	canonical, err := filepath.EvalSymlinks(abs)
	if err != nil || canonical != abs {
		return nil, errors.New("approved store path must be canonical")
	}
	storeDir = abs

	s := &ApprovedArtifactStore{
		storeDir:  storeDir,
		indexPath: filepath.Join(storeDir, "index.json"),
		entries:   make(map[string]*CacheEntry),
	}

	if err := s.loadIndex(); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			if err := s.saveIndex(); err != nil {
				return nil, fmt.Errorf("initialize approved store index: %w", err)
			}
		} else {
			return nil, fmt.Errorf("load approved store index: %w", err)
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

	info, err := os.Lstat(s.indexPath)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return errors.New("approved store index must be a regular file")
	}
	indexFile, _, err := openRegularNoFollow(s.indexPath)
	if err != nil {
		return err
	}
	data, err := io.ReadAll(io.LimitReader(indexFile, 16<<20))
	closeErr := indexFile.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}

	var list []*CacheEntry
	if err := json.Unmarshal(data, &list); err != nil {
		return err
	}
	if list == nil {
		return errors.New("approved store index must be a JSON array")
	}

	s.entries = make(map[string]*CacheEntry)
	for _, entry := range list {
		if err := s.validateEntry(entry); err != nil {
			return err
		}
		key := s.cacheKey(entry.CanonicalRepo, entry.CommitSHA)
		if _, duplicate := s.entries[key]; duplicate {
			return errors.New("duplicate repository commit in approved store index")
		}
		s.entries[key] = entry
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

	return atomicStoreWrite(s.indexPath, data, 0640)
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

	if entry.CanonicalRepo != canonicalRepo || entry.CommitSHA != commitSHA || s.validateEntry(entry) != nil {
		return nil, false
	}
	copy := *entry
	return &copy, true
}

// StoreApproved sao chép gói archive đã quét vào Approved Artifact Store
func (s *ApprovedArtifactStore) StoreApproved(canonicalRepo, commitSHA, scannerVer, dbVer, approvedBy string, qw *QuarantineWorkspace) (*CacheEntry, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if qw == nil {
		return nil, errors.New("quarantine artifact is required")
	}
	canonical, _, _, err := NormalizeAndValidateURL(canonicalRepo)
	if err != nil || canonical != canonicalRepo {
		return nil, errors.New("repository URL is not canonical")
	}
	if !validCommitSHA(commitSHA) {
		return nil, errors.New("approved commit SHA is invalid")
	}
	if !validSHA256(qw.DigestSHA256) {
		return nil, errors.New("quarantine artifact digest is invalid")
	}
	digest := strings.ToLower(qw.DigestSHA256)
	key := s.cacheKey(canonicalRepo, commitSHA)
	if existing, ok := s.entries[key]; ok {
		if err := s.validateEntry(existing); err != nil {
			return nil, fmt.Errorf("existing approved entry is invalid: %w", err)
		}
		if !strings.EqualFold(existing.ArtifactDigest, digest) {
			return nil, errors.New("approved commit is already bound to a different artifact digest")
		}
		if qw.BundleFile != "" {
			bundleDigest := strings.ToLower(qw.BundleDigest)
			if !validSHA256(bundleDigest) {
				return nil, errors.New("quarantine Git bundle digest is invalid")
			}
			bundlePath, bundleSize, err := publishContentAddressed(s.storeDir, qw.BundleFile, bundleDigest, ".bundle")
			if err != nil {
				return nil, fmt.Errorf("publish approved Git bundle: %w", err)
			}
			if existing.BundlePath != "" && (existing.BundleDigest != bundleDigest || existing.BundlePath != bundlePath) {
				return nil, errors.New("approved commit is already bound to a different Git bundle")
			}
			if existing.BundlePath == "" {
				previous := *existing
				existing.BundleDigest, existing.BundlePath, existing.BundleSize = bundleDigest, bundlePath, bundleSize
				if err := s.saveIndex(); err != nil {
					*existing = previous
					return nil, fmt.Errorf("persist approved Git bundle index: %w", err)
				}
			}
		}
		copy := *existing
		return &copy, nil
	}

	// Lưu tệp content-addressed theo SHA-256 digest
	targetFilename := fmt.Sprintf("%s.tar.gz", digest)
	targetPath := filepath.Join(s.storeDir, targetFilename)

	src, _, err := openRegularNoFollow(qw.ArchiveFile)
	if err != nil {
		return nil, fmt.Errorf("quarantine artifact must be a regular file: %w", err)
	}
	defer src.Close()

	tmp, err := os.CreateTemp(s.storeDir, ".artifact-*")
	if err != nil {
		return nil, fmt.Errorf("create staged artifact: %w", err)
	}
	tmpPath := tmp.Name()
	defer os.Remove(tmpPath)
	if err := tmp.Chmod(0640); err != nil {
		tmp.Close()
		return nil, err
	}
	hasher := sha256.New()

	size, err := io.Copy(io.MultiWriter(tmp, hasher), src)
	if err != nil {
		tmp.Close()
		return nil, fmt.Errorf("lỗi copy artifact: %w", err)
	}
	if hex.EncodeToString(hasher.Sum(nil)) != digest {
		tmp.Close()
		return nil, errors.New("quarantine artifact digest mismatch")
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return nil, err
	}
	if err := tmp.Close(); err != nil {
		return nil, err
	}
	if existing, err := os.Lstat(targetPath); err == nil {
		if !existing.Mode().IsRegular() || !fileHasDigest(targetPath, digest) {
			return nil, errors.New("content-addressed artifact collision or tampering")
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	} else if err := unix.Renameat2(unix.AT_FDCWD, tmpPath, unix.AT_FDCWD, targetPath, unix.RENAME_NOREPLACE); err != nil {
		return nil, fmt.Errorf("publish approved artifact: %w", err)
	}
	if err := syncStoreDir(s.storeDir); err != nil {
		return nil, fmt.Errorf("sync approved artifact: %w", err)
	}
	var bundlePath, bundleDigest string
	var bundleSize int64
	if qw.BundleFile != "" {
		bundleDigest = strings.ToLower(qw.BundleDigest)
		if !validSHA256(bundleDigest) {
			return nil, errors.New("quarantine Git bundle digest is invalid")
		}
		bundlePath, bundleSize, err = publishContentAddressed(s.storeDir, qw.BundleFile, bundleDigest, ".bundle")
		if err != nil {
			return nil, fmt.Errorf("publish approved Git bundle: %w", err)
		}
	}

	entry := &CacheEntry{
		CanonicalRepo:  canonicalRepo,
		CommitSHA:      commitSHA,
		ArtifactDigest: digest,
		ArtifactPath:   targetPath,
		ArtifactSize:   size,
		BundleDigest:   bundleDigest,
		BundlePath:     bundlePath,
		BundleSize:     bundleSize,
		PolicyVersion:  CurrentPolicyVersion,
		ScannerVersion: scannerVer,
		DBVersion:      dbVer,
		ApprovedAt:     time.Now(),
		ApprovedBy:     approvedBy,
	}

	previous, hadPrevious := s.entries[key]
	s.entries[key] = entry
	if err := s.saveIndex(); err != nil {
		if hadPrevious {
			s.entries[key] = previous
		} else {
			delete(s.entries, key)
		}
		return nil, fmt.Errorf("lỗi lưu index approved store: %w", err)
	}

	return entry, nil
}

func publishContentAddressed(storeDir, sourcePath, digest, extension string) (string, int64, error) {
	if !validSHA256(digest) || (extension != ".bundle" && extension != ".tar.gz") {
		return "", 0, errors.New("invalid content-addressed artifact metadata")
	}
	src, _, err := openRegularNoFollow(sourcePath)
	if err != nil {
		return "", 0, fmt.Errorf("artifact must be a regular file: %w", err)
	}
	defer src.Close()
	target := filepath.Join(storeDir, strings.ToLower(digest)+extension)
	if info, err := os.Lstat(target); err == nil {
		if !info.Mode().IsRegular() || !fileHasDigest(target, digest) {
			return "", 0, errors.New("content-addressed artifact collision or tampering")
		}
		return target, info.Size(), nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return "", 0, err
	}
	tmp, err := os.CreateTemp(storeDir, ".approved-*")
	if err != nil {
		return "", 0, err
	}
	tmpPath := tmp.Name()
	defer os.Remove(tmpPath)
	if err := tmp.Chmod(0640); err != nil {
		_ = tmp.Close()
		return "", 0, err
	}
	hasher := sha256.New()
	size, copyErr := io.Copy(io.MultiWriter(tmp, hasher), src)
	if copyErr == nil && hex.EncodeToString(hasher.Sum(nil)) != strings.ToLower(digest) {
		copyErr = errors.New("artifact digest mismatch")
	}
	if copyErr == nil {
		copyErr = tmp.Sync()
	}
	closeErr := tmp.Close()
	if copyErr != nil {
		return "", 0, copyErr
	}
	if closeErr != nil {
		return "", 0, closeErr
	}
	if err := unix.Renameat2(unix.AT_FDCWD, tmpPath, unix.AT_FDCWD, target, unix.RENAME_NOREPLACE); err != nil {
		if existing, statErr := os.Lstat(target); statErr != nil || !existing.Mode().IsRegular() || !fileHasDigest(target, digest) {
			return "", 0, fmt.Errorf("publish content-addressed artifact: %w", err)
		}
	}
	if err := syncStoreDir(storeDir); err != nil {
		return "", 0, err
	}
	return target, size, nil
}

func (s *ApprovedArtifactStore) OpenBundle(canonicalRepo, commitSHA string) (*os.File, *CacheEntry, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	entry := s.entries[s.cacheKey(canonicalRepo, commitSHA)]
	if entry == nil || s.validateEntry(entry) != nil || entry.BundlePath == "" || !validSHA256(entry.BundleDigest) {
		return nil, nil, errors.New("approved Git bundle is unavailable")
	}
	expectedPath := filepath.Join(s.storeDir, strings.ToLower(entry.BundleDigest)+".bundle")
	if entry.BundlePath != expectedPath {
		return nil, nil, errors.New("approved Git bundle path is invalid")
	}
	f, info, err := openRegularNoFollow(expectedPath)
	if err != nil {
		return nil, nil, err
	}
	if info.Size() != entry.BundleSize {
		_ = f.Close()
		return nil, nil, errors.New("approved Git bundle size mismatch")
	}
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		_ = f.Close()
		return nil, nil, err
	}
	if hex.EncodeToString(h.Sum(nil)) != strings.ToLower(entry.BundleDigest) {
		_ = f.Close()
		return nil, nil, errors.New("approved Git bundle digest mismatch")
	}
	if _, err := f.Seek(0, io.SeekStart); err != nil {
		_ = f.Close()
		return nil, nil, err
	}
	copy := *entry
	return f, &copy, nil
}

func (s *ApprovedArtifactStore) validateEntry(entry *CacheEntry) error {
	if entry == nil {
		return errors.New("nil approved artifact entry")
	}
	canonical, _, _, err := NormalizeAndValidateURL(entry.CanonicalRepo)
	if err != nil || canonical != entry.CanonicalRepo {
		return errors.New("invalid approved repository URL")
	}
	if !validCommitSHA(entry.CommitSHA) || !validSHA256(entry.ArtifactDigest) {
		return errors.New("invalid approved commit or artifact digest")
	}
	expectedPath := filepath.Join(s.storeDir, strings.ToLower(entry.ArtifactDigest)+".tar.gz")
	if entry.ArtifactPath != expectedPath {
		return errors.New("approved artifact path escapes content-addressed store")
	}
	info, err := os.Lstat(expectedPath)
	if err != nil || !info.Mode().IsRegular() || info.Size() != entry.ArtifactSize {
		return errors.New("approved artifact missing or inconsistent")
	}
	if !fileHasDigest(expectedPath, entry.ArtifactDigest) {
		return errors.New("approved artifact digest verification failed")
	}
	if entry.BundlePath != "" || entry.BundleDigest != "" || entry.BundleSize != 0 {
		expectedBundlePath := filepath.Join(s.storeDir, strings.ToLower(entry.BundleDigest)+".bundle")
		bundleInfo, err := os.Lstat(expectedBundlePath)
		if !validSHA256(entry.BundleDigest) || entry.BundlePath != expectedBundlePath || err != nil || !bundleInfo.Mode().IsRegular() || bundleInfo.Size() != entry.BundleSize {
			return errors.New("approved Git bundle missing or inconsistent")
		}
		if !fileHasDigest(expectedBundlePath, entry.BundleDigest) {
			return errors.New("approved Git bundle digest verification failed")
		}
	}
	return nil
}

func validSHA256(value string) bool {
	if len(value) != sha256.Size*2 {
		return false
	}
	_, err := hex.DecodeString(value)
	return err == nil
}
func validCommitSHA(value string) bool {
	if len(value) != 40 && len(value) != 64 {
		return false
	}
	_, err := hex.DecodeString(value)
	return err == nil
}
func fileHasDigest(path, expected string) bool {
	f, _, err := openRegularNoFollow(path)
	if err != nil {
		return false
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return false
	}
	return hex.EncodeToString(h.Sum(nil)) == strings.ToLower(expected)
}

func openRegularNoFollow(path string) (*os.File, os.FileInfo, error) {
	fd, err := unix.Open(path, unix.O_RDONLY|unix.O_CLOEXEC|unix.O_NOFOLLOW, 0)
	if err != nil {
		return nil, nil, err
	}
	f := os.NewFile(uintptr(fd), path)
	info, err := f.Stat()
	if err != nil {
		_ = f.Close()
		return nil, nil, err
	}
	if !info.Mode().IsRegular() {
		_ = f.Close()
		return nil, nil, errors.New("path is not a regular file")
	}
	return f, info, nil
}
func atomicStoreWrite(path string, data []byte, mode os.FileMode) error {
	dir := filepath.Dir(path)
	f, err := os.CreateTemp(dir, ".index-*")
	if err != nil {
		return err
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if err = f.Chmod(mode); err == nil {
		_, err = f.Write(data)
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
	if err := unix.Renameat2(unix.AT_FDCWD, tmp, unix.AT_FDCWD, path, 0); err != nil {
		return err
	}
	return syncStoreDir(dir)
}
func syncStoreDir(dir string) error {
	f, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer f.Close()
	return f.Sync()
}
