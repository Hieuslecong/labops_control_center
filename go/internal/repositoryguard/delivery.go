package repositoryguard

import (
	"archive/tar"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path"
	"path/filepath"
	"strings"

	"golang.org/x/sys/unix"
)

const (
	maxArtifactBytes  int64 = 128 << 20
	maxExpandedBytes  int64 = 1 << 30
	maxArchiveEntries       = 10000
)

// DeliverArtifact safely publishes a verified archive into a new destination directory.
// Callers must resolve destDir from a trusted workspace registry and bind the digest
// to the approved repository commit before calling this helper. The public delivery
// endpoint remains disabled until that registry integration exists.
func DeliverArtifact(artifactPath, expectedDigest, destDir string) error {
	if artifactPath == "" || destDir == "" {
		return errors.New("artifact and destination are required")
	}
	if len(expectedDigest) != sha256.Size*2 {
		return errors.New("expected SHA-256 digest is invalid")
	}
	if _, err := hex.DecodeString(expectedDigest); err != nil {
		return errors.New("expected SHA-256 digest is invalid")
	}

	absDest, err := filepath.Abs(destDir)
	if err != nil {
		return fmt.Errorf("resolve destination: %w", err)
	}
	parent := filepath.Dir(absDest)
	if filepath.Base(absDest) == "." || filepath.Base(absDest) == string(filepath.Separator) {
		return errors.New("destination must name a new directory")
	}
	parentInfo, err := os.Lstat(parent)
	if err != nil {
		return fmt.Errorf("destination parent unavailable: %w", err)
	}
	if !parentInfo.IsDir() || parentInfo.Mode()&os.ModeSymlink != 0 {
		return errors.New("destination parent must be a real directory")
	}
	canonicalParent, err := filepath.EvalSymlinks(parent)
	if err != nil || canonicalParent != parent {
		return errors.New("destination parent is not canonical")
	}
	if _, err := os.Lstat(absDest); err == nil {
		return errors.New("destination already exists")
	} else if !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("inspect destination: %w", err)
	}

	original, artifactInfo, err := openRegularNoFollow(artifactPath)
	if err != nil {
		return fmt.Errorf("artifact must be a regular file without symlink traversal: %w", err)
	}
	defer original.Close()
	if artifactInfo.Size() < 0 || artifactInfo.Size() > maxArtifactBytes {
		return fmt.Errorf("compressed artifact exceeds %d byte limit", maxArtifactBytes)
	}

	// Snapshot and hash the same bytes that will be extracted, avoiding a hash/read TOCTOU.
	archiveCopy, err := os.CreateTemp(parent, ".labops-artifact-*")
	if err != nil {
		return fmt.Errorf("create private artifact snapshot: %w", err)
	}
	archiveCopyPath := archiveCopy.Name()
	defer os.Remove(archiveCopyPath)
	if err := archiveCopy.Chmod(0600); err != nil {
		archiveCopy.Close()
		return err
	}
	hasher := sha256.New()
	n, copyErr := io.Copy(io.MultiWriter(archiveCopy, hasher), io.LimitReader(original, maxArtifactBytes+1))
	if copyErr != nil {
		archiveCopy.Close()
		return fmt.Errorf("snapshot artifact: %w", copyErr)
	}
	if n > maxArtifactBytes {
		archiveCopy.Close()
		return fmt.Errorf("compressed artifact exceeds %d byte limit", maxArtifactBytes)
	}
	if !strings.EqualFold(hex.EncodeToString(hasher.Sum(nil)), expectedDigest) {
		archiveCopy.Close()
		return errors.New("artifact digest does not match approved digest")
	}
	if err := archiveCopy.Sync(); err != nil {
		archiveCopy.Close()
		return fmt.Errorf("sync artifact snapshot: %w", err)
	}
	if _, err := archiveCopy.Seek(0, io.SeekStart); err != nil {
		archiveCopy.Close()
		return err
	}

	stage, err := os.MkdirTemp(parent, ".labops-delivery-*")
	if err != nil {
		archiveCopy.Close()
		return fmt.Errorf("create delivery staging directory: %w", err)
	}
	published := false
	defer func() {
		if !published {
			_ = os.RemoveAll(stage)
		}
	}()
	root, err := os.OpenRoot(stage)
	if err != nil {
		archiveCopy.Close()
		return fmt.Errorf("open staging root: %w", err)
	}
	defer root.Close()
	if err := extractTarGzip(root, archiveCopy); err != nil {
		archiveCopy.Close()
		return err
	}
	if err := archiveCopy.Close(); err != nil {
		return fmt.Errorf("close artifact snapshot: %w", err)
	}

	// No-replace rename prevents an attacker or concurrent delivery from substituting
	// a pre-existing destination between the check above and publication.
	if err := unix.Renameat2(unix.AT_FDCWD, stage, unix.AT_FDCWD, absDest, unix.RENAME_NOREPLACE); err != nil {
		return fmt.Errorf("atomically publish artifact: %w", err)
	}
	published = true
	dir, err := os.Open(parent)
	if err != nil {
		return fmt.Errorf("artifact published but parent sync could not be opened: %w", err)
	}
	if err := dir.Sync(); err != nil {
		_ = dir.Close()
		return fmt.Errorf("artifact published but parent sync failed: %w", err)
	}
	if err := dir.Close(); err != nil {
		return fmt.Errorf("artifact published but parent close failed: %w", err)
	}
	return nil
}

func extractTarGzip(root *os.Root, archive io.Reader) error {
	gz, err := gzip.NewReader(archive)
	if err != nil {
		return fmt.Errorf("invalid gzip artifact: %w", err)
	}
	defer gz.Close()
	// Bound decompressed metadata and padding as well as file payloads.
	limited := &io.LimitedReader{R: gz, N: maxExpandedBytes + int64(maxArchiveEntries*1024) + 1024}
	tr := tar.NewReader(limited)
	seen := make(map[string]struct{})
	var expanded int64
	entries := 0
	for {
		h, err := tr.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return fmt.Errorf("read archive entry: %w", err)
		}
		entries++
		if entries > maxArchiveEntries {
			return fmt.Errorf("archive exceeds %d entry limit", maxArchiveEntries)
		}
		name, err := safeArchivePath(h.Name)
		if err != nil {
			return err
		}
		if _, duplicate := seen[name]; duplicate {
			return fmt.Errorf("duplicate archive path: %s", name)
		}
		seen[name] = struct{}{}
		switch h.Typeflag {
		case tar.TypeDir:
			if h.Size != 0 {
				return fmt.Errorf("directory entry has non-zero size: %s", name)
			}
			if err := root.MkdirAll(name, 0750); err != nil {
				return fmt.Errorf("create directory %s: %w", name, err)
			}
		case tar.TypeReg, tar.TypeRegA:
			if h.Size < 0 || h.Size > maxExpandedBytes-expanded {
				return errors.New("archive exceeds expanded size limit")
			}
			if h.Size > limited.N {
				return errors.New("archive exceeds decompression limit")
			}
			parent := filepath.ToSlash(filepath.Dir(filepath.FromSlash(name)))
			if parent != "." {
				if err := root.MkdirAll(parent, 0750); err != nil {
					return fmt.Errorf("create parent directory: %w", err)
				}
			}
			out, err := root.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0640)
			if err != nil {
				return fmt.Errorf("create extracted file %s: %w", name, err)
			}
			written, copyErr := io.CopyN(out, tr, h.Size)
			if copyErr == nil && written != h.Size {
				copyErr = io.ErrUnexpectedEOF
			}
			if copyErr == nil {
				copyErr = out.Sync()
			}
			closeErr := out.Close()
			if copyErr != nil {
				return fmt.Errorf("extract %s: %w", name, copyErr)
			}
			if closeErr != nil {
				return fmt.Errorf("close extracted file %s: %w", name, closeErr)
			}
			expanded += h.Size
		default:
			return fmt.Errorf("archive entry type %d is not allowed: %s", h.Typeflag, name)
		}
	}
	if limited.N <= 0 {
		return errors.New("archive exceeded decompression limit")
	}
	return nil
}

func safeArchivePath(name string) (string, error) {
	if name == "" || strings.ContainsRune(name, 0) || strings.Contains(name, "\\") || strings.HasPrefix(name, "/") {
		return "", fmt.Errorf("unsafe archive path: %q", name)
	}
	for _, component := range strings.Split(name, "/") {
		if component == ".." {
			return "", fmt.Errorf("path traversal in archive entry: %q", name)
		}
	}
	clean := path.Clean(name)
	if clean == "." || clean == ".." || strings.HasPrefix(clean, "../") || path.IsAbs(clean) {
		return "", fmt.Errorf("unsafe archive path: %q", name)
	}
	return clean, nil
}
