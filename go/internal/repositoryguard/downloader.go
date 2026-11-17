package repositoryguard

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"
)

const (
	MaxRepoSizeBytes int64 = 500 * 1024 * 1024
	MaxFileCount     int   = 30000
)

type QuarantineWorkspace struct {
	BaseDir      string
	SourceDir    string
	ArchiveFile  string
	DigestSHA256 string
	BundleFile   string
	BundleDigest string
	BundleSize   int64
	TotalSize    int64
	FileCount    int
}

// PrepareQuarantine fetches one immutable commit into a private, disposable root.
// Repository symlinks and special archive entries are rejected: scanning and later
// delivery must not interpret filesystem indirections from an untrusted repository.
func PrepareQuarantine(ctx context.Context, baseQuarantineDir, requestID, repoURL, commitSHA string) (*QuarantineWorkspace, error) {
	if !safeGeneratedRequestID.MatchString(requestID) {
		return nil, errors.New("invalid quarantine request ID")
	}
	canonicalRepo, _, _, err := NormalizeAndValidateURL(repoURL)
	if err != nil || canonicalRepo != repoURL {
		return nil, errors.New("repository URL must be canonical HTTPS GitHub URL")
	}
	if !validCommitSHA(commitSHA) {
		return nil, errors.New("commit SHA must be a full Git object ID")
	}
	base, err := filepath.Abs(baseQuarantineDir)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(base, 0750); err != nil {
		return nil, fmt.Errorf("create quarantine root: %w", err)
	}
	baseInfo, err := os.Lstat(base)
	if err != nil || !baseInfo.IsDir() || baseInfo.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("quarantine root must be a real directory")
	}
	canonicalBase, err := filepath.EvalSymlinks(base)
	if err != nil || canonicalBase != base {
		return nil, errors.New("quarantine root must be canonical")
	}
	root, err := os.OpenRoot(base)
	if err != nil {
		return nil, fmt.Errorf("open quarantine root: %w", err)
	}
	defer root.Close()
	if err := root.Mkdir(requestID, 0700); err != nil {
		return nil, fmt.Errorf("create quarantine request root: %w", err)
	}
	baseDir := filepath.Join(base, requestID)
	succeeded := false
	defer func() {
		if !succeeded {
			_ = root.RemoveAll(requestID)
		}
	}()
	sourceDir := filepath.Join(baseDir, "source")
	archivePath := filepath.Join(baseDir, "source.tar.gz")
	bundlePath := filepath.Join(baseDir, "repository.bundle")
	if err := os.Mkdir(sourceDir, 0700); err != nil {
		return nil, fmt.Errorf("create quarantine source: %w", err)
	}

	ctxTimeout, cancel := context.WithTimeout(ctx, 3*time.Minute)
	defer cancel()
	gitDir := filepath.Join(baseDir, ".git_clone_tmp")
	clone := trustedGitCommand(ctxTimeout, "-c", "core.hooksPath=/dev/null", "-c", "protocol.allow=never",
		"-c", "protocol.https.allow=always", "-c", "http.followRedirects=false",
		"clone", "--depth=1", "--filter=blob:none", "--no-checkout", repoURL, gitDir)
	if err := clone.Run(); err != nil {
		fallback := trustedGitCommand(ctxTimeout, "-c", "core.hooksPath=/dev/null", "-c", "protocol.allow=never",
			"-c", "protocol.https.allow=always", "-c", "http.followRedirects=false",
			"clone", "--depth=1", "--no-checkout", repoURL, gitDir)
		if fallbackErr := fallback.Run(); fallbackErr != nil {
			return nil, fmt.Errorf("clone repository into quarantine: %w", fallbackErr)
		}
	}

	fetch := trustedGitCommand(ctxTimeout, "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
		"-c", "http.followRedirects=false", "-C", gitDir, "fetch", "--depth=1", "origin", commitSHA)
	if err := fetch.Run(); err != nil {
		verify := trustedGitCommand(ctxTimeout, "-C", gitDir, "cat-file", "-e", commitSHA+"^{commit}")
		if verify.Run() != nil {
			return nil, fmt.Errorf("fetch approved commit %s: %w", commitSHA, err)
		}
	}
	verify := trustedGitCommand(ctxTimeout, "-C", gitDir, "cat-file", "-e", commitSHA+"^{commit}")
	if err := verify.Run(); err != nil {
		return nil, fmt.Errorf("approved commit is unavailable: %w", err)
	}

	archiveCmd := trustedGitCommand(ctxTimeout, "-C", gitDir, "archive", "--format=tar", commitSHA)
	tarOut, err := archiveCmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("open git archive output: %w", err)
	}
	if err := archiveCmd.Start(); err != nil {
		return nil, fmt.Errorf("start git archive: %w", err)
	}
	totalSize, fileCount, extractErr := extractRepositoryTar(sourceDir, tarOut)
	_ = tarOut.Close()
	waitErr := archiveCmd.Wait()
	if extractErr != nil {
		return nil, extractErr
	}
	if waitErr != nil {
		return nil, fmt.Errorf("git archive failed: %w", waitErr)
	}
	if err := createGitBundle(ctxTimeout, gitDir, bundlePath, commitSHA); err != nil {
		return nil, fmt.Errorf("create approved Git bundle: %w", err)
	}
	bundleDigest, err := digestRegularFile(bundlePath)
	if err != nil {
		return nil, fmt.Errorf("hash approved Git bundle: %w", err)
	}
	bundleInfo, err := os.Lstat(bundlePath)
	if err != nil {
		return nil, fmt.Errorf("inspect approved Git bundle: %w", err)
	}

	if err := os.RemoveAll(gitDir); err != nil {
		return nil, fmt.Errorf("remove temporary git metadata: %w", err)
	}
	if err := createTarGz(sourceDir, archivePath); err != nil {
		return nil, fmt.Errorf("create approved snapshot archive: %w", err)
	}
	digest, err := digestRegularFile(archivePath)
	if err != nil {
		return nil, fmt.Errorf("hash quarantine archive: %w", err)
	}
	succeeded = true
	return &QuarantineWorkspace{
		BaseDir: baseDir, SourceDir: sourceDir, ArchiveFile: archivePath,
		DigestSHA256: digest, BundleFile: bundlePath, BundleDigest: bundleDigest,
		BundleSize: bundleInfo.Size(), TotalSize: totalSize, FileCount: fileCount,
	}, nil
}

func extractRepositoryTar(sourceDir string, archive io.Reader) (int64, int, error) {
	root, err := os.OpenRoot(sourceDir)
	if err != nil {
		return 0, 0, err
	}
	defer root.Close()
	tr := tar.NewReader(archive)
	seen := make(map[string]struct{})
	var total int64
	count := 0
	for {
		h, err := tr.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return total, count, fmt.Errorf("read repository archive: %w", err)
		}
		count++
		if count > MaxFileCount {
			return total, count, fmt.Errorf("repository exceeds %d entry limit", MaxFileCount)
		}
		name, err := safeArchivePath(h.Name)
		if err != nil {
			return total, count, err
		}
		if _, duplicate := seen[name]; duplicate {
			return total, count, fmt.Errorf("duplicate repository path: %s", name)
		}
		seen[name] = struct{}{}
		switch h.Typeflag {
		case tar.TypeDir:
			if h.Size != 0 {
				return total, count, fmt.Errorf("directory entry has content: %s", name)
			}
			if err := root.MkdirAll(name, 0750); err != nil {
				return total, count, fmt.Errorf("create repository directory: %w", err)
			}
		case tar.TypeReg, tar.TypeRegA:
			if h.Size < 0 || h.Size > MaxRepoSizeBytes-total {
				return total, count, fmt.Errorf("repository exceeds %d byte expanded size limit", MaxRepoSizeBytes)
			}
			parent := filepath.ToSlash(filepath.Dir(filepath.FromSlash(name)))
			if parent != "." {
				if err := root.MkdirAll(parent, 0750); err != nil {
					return total, count, err
				}
			}
			out, err := root.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0640)
			if err != nil {
				return total, count, fmt.Errorf("create repository file: %w", err)
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
				return total, count, fmt.Errorf("extract repository file: %w", copyErr)
			}
			if closeErr != nil {
				return total, count, closeErr
			}
			total += h.Size
		default:
			return total, count, fmt.Errorf("repository archive entry type %d is not allowed: %s", h.Typeflag, name)
		}
	}
	return total, count, nil
}

func createTarGz(srcDir, tarGzPath string) error {
	out, err := os.OpenFile(tarGzPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	gz := gzip.NewWriter(out)
	tarWriter := tar.NewWriter(gz)
	walkErr := filepath.WalkDir(srcDir, func(filePath string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if filePath == srcDir {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		if info.Mode()&os.ModeSymlink != 0 || (!info.IsDir() && !info.Mode().IsRegular()) {
			return fmt.Errorf("unsupported quarantine file type: %s", filePath)
		}
		rel, err := filepath.Rel(srcDir, filePath)
		if err != nil {
			return err
		}
		header, err := tar.FileInfoHeader(info, "")
		if err != nil {
			return err
		}
		header.Name = filepath.ToSlash(rel)
		if err := tarWriter.WriteHeader(header); err != nil {
			return err
		}
		if info.Mode().IsRegular() {
			f, err := os.Open(filePath)
			if err != nil {
				return err
			}
			_, copyErr := io.Copy(tarWriter, f)
			closeErr := f.Close()
			if copyErr != nil {
				return copyErr
			}
			if closeErr != nil {
				return closeErr
			}
		}
		return nil
	})
	if walkErr != nil {
		_ = tarWriter.Close()
		_ = gz.Close()
		_ = out.Close()
		return walkErr
	}
	if err := tarWriter.Close(); err != nil {
		_ = gz.Close()
		_ = out.Close()
		return err
	}
	if err := gz.Close(); err != nil {
		_ = out.Close()
		return err
	}
	if err := out.Sync(); err != nil {
		_ = out.Close()
		return err
	}
	return out.Close()
}

func digestRegularFile(filePath string) (string, error) {
	f, _, err := openRegularNoFollow(filePath)
	if err != nil {
		return "", fmt.Errorf("artifact must be a regular file: %w", err)
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func (qw *QuarantineWorkspace) Cleanup() error {
	return qw.removeOwnedPath("source")
}

func (qw *QuarantineWorkspace) PurgeAll() error {
	return qw.removeOwnedPath("")
}

func (qw *QuarantineWorkspace) removeOwnedPath(child string) error {
	if qw == nil || qw.BaseDir == "" {
		return errors.New("invalid quarantine cleanup target")
	}
	base := filepath.Clean(qw.BaseDir)
	requestID := filepath.Base(base)
	if !safeGeneratedRequestID.MatchString(requestID) {
		return errors.New("invalid quarantine cleanup request ID")
	}
	parent := filepath.Dir(base)
	canonicalParent, err := filepath.EvalSymlinks(parent)
	if err != nil || canonicalParent != parent {
		return errors.New("quarantine cleanup parent is not canonical")
	}
	if child == "source" && qw.SourceDir != filepath.Join(base, "source") {
		return errors.New("quarantine source path does not match request root")
	}
	root, err := os.OpenRoot(parent)
	if err != nil {
		return err
	}
	defer root.Close()
	name := requestID
	if child != "" {
		name = filepath.ToSlash(filepath.Join(requestID, child))
	}
	return root.RemoveAll(name)
}
