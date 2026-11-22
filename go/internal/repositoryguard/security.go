package repositoryguard

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

const (
	MaxArchivePathDepth         = 32
	BundleSubdirName            = ".labops_bundle"
	BundleFileName              = "repo.bundle"
	ManifestFileName            = "manifest.json"
	ReapprovalRequiredOriginURL = "labops-broker://reapproval-required"
)

// RepoManifest records immutable Git provenance sealed inside each quarantine archive.
type RepoManifest struct {
	RepoURL       string `json:"repo_url"`
	CanonicalRepo string `json:"canonical_repo"`
	CommitSHA     string `json:"commit_sha"`
	BundleSHA256  string `json:"bundle_sha256"`
	PolicyVersion string `json:"policy_version"`
}

var forbiddenDestPrefixes = []string{
	"/",
	"/etc",
	"/proc",
	"/sys",
	"/root",
	"/var/run",
	"/run",
	"/dev",
	"/boot",
	"/usr",
	"/bin",
	"/sbin",
	"/lib",
	"/lib64",
	"/opt",
}

// SafeGitConfigArgs returns git `-c` flags that neutralize hooks, file:// transport, submodules, and LFS smudge filters.
func SafeGitConfigArgs() []string {
	return []string{
		"-c", "core.hooksPath=/dev/null",
		"-c", "protocol.file.allow=never",
		"-c", "submodule.recurse=false",
		"-c", "filter.lfs.smudge=",
		"-c", "filter.lfs.process=",
		"-c", "filter.lfs.required=false",
	}
}

// SafeGitEnv returns a hardened environment for git subprocesses with prompts and host gitconfig disabled.
func SafeGitEnv() []string {
	env := os.Environ()
	filtered := make([]string, 0, len(env)+4)
	for _, entry := range env {
		if strings.HasPrefix(entry, "GIT_TERMINAL_PROMPT=") ||
			strings.HasPrefix(entry, "GIT_CONFIG_NOSYSTEM=") ||
			strings.HasPrefix(entry, "GIT_CONFIG_GLOBAL=") ||
			strings.HasPrefix(entry, "GIT_ASKPASS=") {
			continue
		}
		filtered = append(filtered, entry)
	}
	return append(filtered,
		"GIT_TERMINAL_PROMPT=0",
		"GIT_CONFIG_NOSYSTEM=1",
		"GIT_CONFIG_GLOBAL=/dev/null",
	)
}

func runGit(ctx context.Context, dir string, args ...string) ([]byte, error) {
	fullArgs := append(SafeGitConfigArgs(), args...)
	cmd := exec.CommandContext(ctx, "git", fullArgs...)
	if dir != "" {
		cmd.Dir = dir
	}
	cmd.Env = SafeGitEnv()
	out, err := cmd.CombinedOutput()
	if err != nil {
		return out, fmt.Errorf("git %s failed: %w (%s)", strings.Join(args, " "), err, strings.TrimSpace(string(out)))
	}
	return out, nil
}

// ValidateArchivePath verifies an archive entry path is relative, traversal-free, and within MaxArchivePathDepth.
func ValidateArchivePath(rawPath string) (string, error) {
	if rawPath == "" {
		return "", errors.New("empty archive entry path")
	}
	if strings.ContainsRune(rawPath, '\x00') || strings.ContainsRune(rawPath, '\\') {
		return "", fmt.Errorf("unsafe characters in archive path: %q", rawPath)
	}
	if strings.HasPrefix(rawPath, "/") || filepath.IsAbs(rawPath) {
		return "", fmt.Errorf("absolute path forbidden in archive: %s", rawPath)
	}

	slashRaw := filepath.ToSlash(rawPath)
	for _, seg := range strings.Split(slashRaw, "/") {
		if seg == ".." {
			return "", fmt.Errorf("path traversal segment forbidden in archive: %s", rawPath)
		}
	}

	trimmed := strings.TrimSuffix(slashRaw, "/")
	if trimmed == "" || trimmed == "." {
		return ".", nil
	}

	clean := filepath.ToSlash(filepath.Clean(trimmed))
	if clean == ".." || strings.HasPrefix(clean, "../") || strings.HasPrefix(clean, "/") {
		return "", fmt.Errorf("path traversal detected in archive: %s", rawPath)
	}

	segments := strings.Split(clean, "/")
	if len(segments) > MaxArchivePathDepth {
		return "", fmt.Errorf("archive path depth %d exceeds limit %d: %s", len(segments), MaxArchivePathDepth, rawPath)
	}
	return clean, nil
}

// LstatNoSymlinkChain verifies no existing path component from rootDir down to targetPath is a symlink.
func LstatNoSymlinkChain(rootDir, targetPath string) error {
	cleanRoot := filepath.Clean(rootDir)
	cleanTarget := filepath.Clean(targetPath)

	rel, err := filepath.Rel(cleanRoot, cleanTarget)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return fmt.Errorf("target outside root boundary: %s", targetPath)
	}

	if info, err := os.Lstat(cleanRoot); err == nil {
		if info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("root directory itself is a symlink: %s", cleanRoot)
		}
	} else if !os.IsNotExist(err) {
		return err
	}

	if rel == "." {
		return nil
	}

	curr := cleanRoot
	for _, part := range strings.Split(rel, string(filepath.Separator)) {
		if part == "" || part == "." {
			continue
		}
		curr = filepath.Join(curr, part)
		info, err := os.Lstat(curr)
		if os.IsNotExist(err) {
			break
		}
		if err != nil {
			return err
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("symlink detected in path chain: %s", curr)
		}
	}
	return nil
}

// LstatNoSymlinkChainFromRoot verifies no existing ancestor or target component on an absolute path is a symlink.
func LstatNoSymlinkChainFromRoot(absPath string) error {
	clean := filepath.Clean(absPath)
	if !filepath.IsAbs(clean) {
		return fmt.Errorf("path must be absolute: %s", absPath)
	}
	parts := strings.Split(strings.TrimPrefix(clean, string(filepath.Separator)), string(filepath.Separator))
	curr := string(filepath.Separator)
	for _, part := range parts {
		if part == "" {
			continue
		}
		curr = filepath.Join(curr, part)
		info, err := os.Lstat(curr)
		if os.IsNotExist(err) {
			break
		}
		if err != nil {
			return err
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("symlink detected along destination path: %s", curr)
		}
	}
	return nil
}

// ValidateArchiveSymlink verifies a tar symlink entry cannot escape rootDir or traverse symlink chains.
func ValidateArchiveSymlink(rootDir, targetPath, linkTarget string) error {
	if linkTarget == "" {
		return errors.New("empty symlink target forbidden")
	}
	if strings.ContainsRune(linkTarget, '\x00') || strings.ContainsRune(linkTarget, '\\') {
		return fmt.Errorf("unsafe characters in symlink target: %q", linkTarget)
	}
	if strings.HasPrefix(linkTarget, "/") || filepath.IsAbs(linkTarget) {
		return fmt.Errorf("absolute symlink target forbidden: %s -> %s", targetPath, linkTarget)
	}

	if err := LstatNoSymlinkChain(rootDir, filepath.Dir(targetPath)); err != nil {
		return fmt.Errorf("parent path contains symlink: %w", err)
	}

	resolvedDest := filepath.Clean(filepath.Join(filepath.Dir(targetPath), linkTarget))
	relDest, err := filepath.Rel(rootDir, resolvedDest)
	if err != nil || relDest == ".." || strings.HasPrefix(relDest, ".."+string(filepath.Separator)) {
		return fmt.Errorf("symlink escape forbidden: %s -> %s", targetPath, linkTarget)
	}

	if err := LstatNoSymlinkChain(rootDir, resolvedDest); err != nil {
		return fmt.Errorf("chained symlink target forbidden: %w", err)
	}
	return nil
}

// ValidateDestPath verifies destination directory safety, blocks system roots and symlinks, and enforces allowedRoots if provided.
func ValidateDestPath(destDir string, allowedRoots []string) (string, error) {
	if strings.TrimSpace(destDir) == "" {
		return "", errors.New("destination path cannot be empty")
	}
	if strings.ContainsRune(destDir, '\x00') {
		return "", errors.New("null byte in destination path")
	}
	if !filepath.IsAbs(destDir) {
		return "", fmt.Errorf("destination path must be absolute: %s", destDir)
	}
	slashRaw := filepath.ToSlash(destDir)
	for _, seg := range strings.Split(slashRaw, "/") {
		if seg == ".." {
			return "", fmt.Errorf("path traversal segment forbidden in destination: %s", destDir)
		}
	}

	cleanDest := filepath.Clean(destDir)
	for _, prefix := range forbiddenDestPrefixes {
		if prefix == "/" {
			if cleanDest == "/" {
				return "", fmt.Errorf("forbidden host root destination: %s", cleanDest)
			}
			continue
		}
		if cleanDest == prefix || strings.HasPrefix(cleanDest, prefix+"/") {
			return "", fmt.Errorf("forbidden host system destination: %s", cleanDest)
		}
	}

	if cleanDest == "/home" || cleanDest == "/home/.ssh" || strings.Contains(cleanDest, "/.ssh") {
		return "", fmt.Errorf("forbidden sensitive destination path: %s", cleanDest)
	}

	if len(allowedRoots) > 0 {
		matchedRoot := ""
		for _, root := range allowedRoots {
			if strings.TrimSpace(root) == "" {
				continue
			}
			cleanRoot := filepath.Clean(root)
			rel, err := filepath.Rel(cleanRoot, cleanDest)
			if err == nil && rel != "." && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
				matchedRoot = cleanRoot
				break
			}
		}
		if matchedRoot == "" {
			return "", fmt.Errorf("destination %s is outside allowed workspace roots", cleanDest)
		}
		if err := LstatNoSymlinkChain(matchedRoot, cleanDest); err != nil {
			return "", err
		}
	}

	if err := LstatNoSymlinkChainFromRoot(cleanDest); err != nil {
		return "", err
	}

	return cleanDest, nil
}

// HashFileSHA256 computes the lowercase hex SHA-256 digest of a file.
func HashFileSHA256(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()

	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// CopyAndVerifySHA256 copies srcPath into a private temp file under tmpDir, enforcing maxBytes and optional expectedDigest.
func CopyAndVerifySHA256(srcPath, tmpDir, expectedDigest string, maxBytes int64) (string, string, error) {
	info, err := os.Lstat(srcPath)
	if err != nil {
		return "", "", fmt.Errorf("cannot inspect artifact file: %w", err)
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return "", "", errors.New("artifact path must reference a regular non-symlink file")
	}

	srcFile, err := os.Open(srcPath)
	if err != nil {
		return "", "", fmt.Errorf("cannot open artifact file: %w", err)
	}
	defer srcFile.Close()

	tmpFile, err := os.CreateTemp(tmpDir, "labops-verify-*.tar.gz")
	if err != nil {
		return "", "", fmt.Errorf("cannot create staging artifact copy: %w", err)
	}
	tmpPath := tmpFile.Name()
	cleanupTmp := true
	defer func() {
		_ = tmpFile.Close()
		if cleanupTmp {
			_ = os.Remove(tmpPath)
		}
	}()

	hasher := sha256.New()
	var limited io.Reader = srcFile
	if maxBytes > 0 {
		limited = io.LimitReader(srcFile, maxBytes+1)
	}
	written, err := io.Copy(io.MultiWriter(tmpFile, hasher), limited)
	if err != nil {
		return "", "", fmt.Errorf("error copying artifact for verification: %w", err)
	}
	if maxBytes > 0 && written > maxBytes {
		return "", "", fmt.Errorf("artifact size exceeds maximum limit (%d bytes)", maxBytes)
	}
	if err := tmpFile.Sync(); err != nil {
		return "", "", err
	}

	actualDigest := hex.EncodeToString(hasher.Sum(nil))
	if expectedDigest != "" && !strings.EqualFold(actualDigest, expectedDigest) {
		return "", "", fmt.Errorf("TOCTOU / Integrity Failure: artifact checksum mismatch (expected: %s, actual: %s)", expectedDigest, actualDigest)
	}

	cleanupTmp = false
	return tmpPath, actualDigest, nil
}

// BuildGitBundleAndManifest exports a deterministic Git bundle and manifest for commitSHA into bundleStageDir/.labops_bundle.
func BuildGitBundleAndManifest(ctx context.Context, gitDir, bundleStageDir, repoURL, canonicalRepo, commitSHA, policyVersion string) (*RepoManifest, error) {
	if _, err := runGit(ctx, gitDir, "rev-parse", "--verify", commitSHA+"^{commit}"); err != nil {
		return nil, fmt.Errorf("exact commit %s not verified in cloned repository: %w", commitSHA, err)
	}

	if _, err := runGit(ctx, gitDir, "update-ref", "refs/heads/main", commitSHA); err != nil {
		return nil, fmt.Errorf("cannot pin refs/heads/main to commit %s: %w", commitSHA, err)
	}

	metaDir := filepath.Join(bundleStageDir, BundleSubdirName)
	if err := os.MkdirAll(metaDir, 0750); err != nil {
		return nil, err
	}

	bundlePath := filepath.Join(metaDir, BundleFileName)
	if _, err := runGit(ctx, gitDir, "bundle", "create", bundlePath, "refs/heads/main"); err != nil {
		return nil, fmt.Errorf("failed creating git bundle: %w", err)
	}

	if err := os.Chmod(bundlePath, 0440); err != nil {
		return nil, err
	}

	bundleDigest, err := HashFileSHA256(bundlePath)
	if err != nil {
		return nil, err
	}

	if policyVersion == "" {
		policyVersion = CurrentPolicyVersion
	}
	if canonicalRepo == "" {
		canonicalRepo = repoURL
	}

	manifest := &RepoManifest{
		RepoURL:       repoURL,
		CanonicalRepo: canonicalRepo,
		CommitSHA:     commitSHA,
		BundleSHA256:  bundleDigest,
		PolicyVersion: policyVersion,
	}

	manifestBytes, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return nil, err
	}
	manifestPath := filepath.Join(metaDir, ManifestFileName)
	if err := os.WriteFile(manifestPath, manifestBytes, 0440); err != nil {
		return nil, err
	}
	return manifest, nil
}

// CreateSealedTarGz packs sourceDir and optional bundleStageDir into archivePath with strict mode 0440.
func CreateSealedTarGz(sourceDir, bundleStageDir, archivePath string, maxBytes int64) error {
	tmpPath := archivePath + ".tmp"
	outFile, err := os.OpenFile(tmpPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0640)
	if err != nil {
		return err
	}
	cleanup := true
	defer func() {
		if cleanup {
			_ = outFile.Close()
			_ = os.Remove(tmpPath)
		}
	}()

	gw := gzip.NewWriter(outFile)
	tw := tar.NewWriter(gw)

	var totalPacked int64
	packTree := func(baseDir string) error {
		if baseDir == "" {
			return nil
		}
		return filepath.Walk(baseDir, func(path string, info os.FileInfo, walkErr error) error {
			if walkErr != nil {
				return walkErr
			}
			rel, err := filepath.Rel(baseDir, path)
			if err != nil || rel == "." {
				return nil
			}
			relSlash := filepath.ToSlash(rel)

			header, err := tar.FileInfoHeader(info, "")
			if err != nil {
				return err
			}
			header.Name = relSlash

			if info.Mode()&os.ModeSymlink != 0 {
				target, err := os.Readlink(path)
				if err != nil {
					return err
				}
				header.Linkname = target
			}

			if err := tw.WriteHeader(header); err != nil {
				return err
			}

			if info.Mode().IsRegular() {
				f, err := os.Open(path)
				if err != nil {
					return err
				}
				var r io.Reader = f
				if maxBytes > 0 {
					remaining := maxBytes - totalPacked
					if remaining < 0 {
						_ = f.Close()
						return fmt.Errorf("archive packing exceeds size limit (%d bytes)", maxBytes)
					}
					r = io.LimitReader(f, remaining+1)
				}
				n, copyErr := io.Copy(tw, r)
				closeErr := f.Close()
				if copyErr != nil {
					return copyErr
				}
				if closeErr != nil {
					return closeErr
				}
				totalPacked += n
				if maxBytes > 0 && totalPacked > maxBytes {
					return fmt.Errorf("archive packing exceeds size limit (%d bytes)", maxBytes)
				}
			}
			return nil
		})
	}

	if err := packTree(sourceDir); err != nil {
		_ = tw.Close()
		_ = gw.Close()
		return err
	}
	if err := packTree(bundleStageDir); err != nil {
		_ = tw.Close()
		_ = gw.Close()
		return err
	}

	if err := tw.Close(); err != nil {
		_ = gw.Close()
		return err
	}
	if err := gw.Close(); err != nil {
		return err
	}
	if err := outFile.Sync(); err != nil {
		return err
	}
	if err := outFile.Close(); err != nil {
		return err
	}
	if err := os.Chmod(tmpPath, 0440); err != nil {
		return err
	}
	if err := os.Rename(tmpPath, archivePath); err != nil {
		return err
	}
	cleanup = false
	return nil
}

// VerifyBundleMetadata validates .labops_bundle/manifest.json and repo.bundle digest inside an extracted stageDir.
func VerifyBundleMetadata(stageDir, expectedCommitSHA string) (*RepoManifest, string, error) {
	metaDir := filepath.Join(stageDir, BundleSubdirName)
	manifestPath := filepath.Join(metaDir, ManifestFileName)
	bundlePath := filepath.Join(metaDir, BundleFileName)

	manifestInfo, err := os.Lstat(manifestPath)
	if err != nil {
		return nil, "", err
	}
	if manifestInfo.Mode()&os.ModeSymlink != 0 || !manifestInfo.Mode().IsRegular() {
		return nil, "", errors.New("manifest.json must be a regular file")
	}

	raw, err := os.ReadFile(manifestPath)
	if err != nil {
		return nil, "", err
	}
	var manifest RepoManifest
	if err := json.Unmarshal(raw, &manifest); err != nil {
		return nil, "", fmt.Errorf("corrupt bundle manifest.json: %w", err)
	}
	if manifest.CommitSHA == "" || manifest.BundleSHA256 == "" {
		return nil, "", errors.New("bundle manifest missing commit_sha or bundle_sha256")
	}
	if expectedCommitSHA != "" && !strings.EqualFold(manifest.CommitSHA, expectedCommitSHA) {
		return nil, "", fmt.Errorf("manifest commit mismatch (expected %s, got %s)", expectedCommitSHA, manifest.CommitSHA)
	}

	bundleInfo, err := os.Lstat(bundlePath)
	if err != nil {
		return nil, "", fmt.Errorf("missing repo.bundle: %w", err)
	}
	if bundleInfo.Mode()&os.ModeSymlink != 0 || !bundleInfo.Mode().IsRegular() {
		return nil, "", errors.New("repo.bundle must be a regular file")
	}

	actualBundleSHA, err := HashFileSHA256(bundlePath)
	if err != nil {
		return nil, "", err
	}
	if !strings.EqualFold(actualBundleSHA, manifest.BundleSHA256) {
		return nil, "", fmt.Errorf("repo.bundle digest mismatch (expected %s, got %s)", manifest.BundleSHA256, actualBundleSHA)
	}

	return &manifest, bundlePath, nil
}

// MaterializeGitWorktreeFromBundle creates a real checked-out git working tree from a verified bundle without hooks.
func MaterializeGitWorktreeFromBundle(ctx context.Context, bundlePath string, manifest *RepoManifest, worktreeDir string, maxBytes int64, maxFiles int) error {
	if err := os.MkdirAll(worktreeDir, 0750); err != nil {
		return err
	}

	if _, err := runGit(ctx, "", "init", worktreeDir); err != nil {
		return err
	}
	if _, err := runGit(ctx, worktreeDir, "symbolic-ref", "HEAD", "refs/heads/main"); err != nil {
		return err
	}
	if _, err := runGit(ctx, worktreeDir, "bundle", "verify", bundlePath); err != nil {
		return fmt.Errorf("bundle verification failed: %w", err)
	}

	unbundleOut, err := runGit(ctx, worktreeDir, "bundle", "unbundle", bundlePath)
	if err != nil {
		return fmt.Errorf("bundle unbundle failed: %w", err)
	}

	commitFound := false
	for _, line := range strings.Split(strings.TrimSpace(string(unbundleOut)), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 1 && strings.EqualFold(fields[0], manifest.CommitSHA) {
			commitFound = true
			break
		}
	}
	if !commitFound {
		return fmt.Errorf("expected commit %s was not unbundled from repo.bundle", manifest.CommitSHA)
	}

	if _, err := runGit(ctx, worktreeDir, "update-ref", "refs/heads/main", manifest.CommitSHA); err != nil {
		return err
	}
	if _, err := runGit(ctx, worktreeDir, "checkout", "-f", "main"); err != nil {
		return err
	}

	// Post-checkout security audit of materialized working tree (excluding .git)
	var totalBytes int64
	var totalFiles int
	err = filepath.Walk(worktreeDir, func(path string, info os.FileInfo, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		rel, err := filepath.Rel(worktreeDir, path)
		if err != nil || rel == "." {
			return nil
		}
		relSlash := filepath.ToSlash(rel)
		if relSlash == ".git" || strings.HasPrefix(relSlash, ".git/") {
			if info.IsDir() && relSlash == ".git" {
				return filepath.SkipDir
			}
			return nil
		}

		if _, err := ValidateArchivePath(relSlash); err != nil {
			return err
		}
		totalFiles++
		if maxFiles > 0 && totalFiles > maxFiles {
			return fmt.Errorf("checked out worktree exceeds file count limit (%d)", maxFiles)
		}
		if info.Mode()&os.ModeSymlink != 0 {
			linkTarget, err := os.Readlink(path)
			if err != nil {
				return err
			}
			if err := ValidateArchiveSymlink(worktreeDir, path, linkTarget); err != nil {
				return err
			}
			return nil
		}
		if info.Mode().IsRegular() {
			totalBytes += info.Size()
			if maxBytes > 0 && totalBytes > maxBytes {
				return fmt.Errorf("checked out worktree exceeds byte limit (%d)", maxBytes)
			}
		}
		return nil
	})
	if err != nil {
		return err
	}

	// Neutralize pull URL to force broker reapproval, while keeping canonical push target if known.
	if _, err := runGit(ctx, worktreeDir, "remote", "add", "origin", ReapprovalRequiredOriginURL); err != nil {
		return err
	}
	if manifest.CanonicalRepo != "" {
		if _, err := runGit(ctx, worktreeDir, "remote", "set-url", "--push", "origin", manifest.CanonicalRepo); err != nil {
			return err
		}
	}

	insideOut, err := runGit(ctx, worktreeDir, "rev-parse", "--is-inside-work-tree")
	if err != nil || strings.TrimSpace(string(insideOut)) != "true" {
		return fmt.Errorf("delivered directory is not a valid git worktree: %w (%s)", err, string(insideOut))
	}
	headOut, err := runGit(ctx, worktreeDir, "rev-parse", "HEAD")
	if err != nil || !strings.EqualFold(strings.TrimSpace(string(headOut)), manifest.CommitSHA) {
		return fmt.Errorf("delivered HEAD mismatch: got %s, expected %s", strings.TrimSpace(string(headOut)), manifest.CommitSHA)
	}
	statusOut, err := runGit(ctx, worktreeDir, "status", "--porcelain")
	if err != nil || bytes.TrimSpace(statusOut) != nil && len(bytes.TrimSpace(statusOut)) > 0 {
		return fmt.Errorf("delivered worktree is dirty or status failed: %w (%s)", err, string(statusOut))
	}

	return nil
}
