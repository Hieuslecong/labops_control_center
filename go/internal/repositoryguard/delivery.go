package repositoryguard

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"
)

// DeliverArtifact extracts an integrity-verified artifact into a validated destination directory.
func DeliverArtifact(artifactPath, expectedDigest, destDir string) error {
	return DeliverArtifactStrict(context.Background(), artifactPath, expectedDigest, "", destDir, nil)
}

// DeliverArtifactStrict verifies SHA-256 without TOCTOU, validates paths/symlinks/limits,
// reconstructs a safe Git working tree from .labops_bundle if present (or required when expectedCommitSHA != ""),
// and atomically commits into destDir.
func DeliverArtifactStrict(ctx context.Context, artifactPath, expectedDigest, expectedCommitSHA, destDir string, allowedRoots []string) error {
	if artifactPath == "" || expectedDigest == "" || destDir == "" {
		return errors.New("artifactPath, expectedDigest, and destDir must not be empty")
	}

	cleanDest, err := ValidateDestPath(destDir, allowedRoots)
	if err != nil {
		return fmt.Errorf("unsafe delivery destination: %w", err)
	}

	parentDir := filepath.Dir(cleanDest)
	if err := os.MkdirAll(parentDir, 0750); err != nil {
		return fmt.Errorf("cannot prepare parent directory %s: %w", parentDir, err)
	}
	if err := LstatNoSymlinkChainFromRoot(cleanDest); err != nil {
		return fmt.Errorf("symlink check failed on destination %s: %w", cleanDest, err)
	}

	if existingInfo, err := os.Lstat(cleanDest); err == nil {
		if existingInfo.Mode()&os.ModeSymlink != 0 || !existingInfo.IsDir() {
			return fmt.Errorf("destination exists and is not a real directory: %s", cleanDest)
		}
		entries, readErr := os.ReadDir(cleanDest)
		if readErr != nil {
			return fmt.Errorf("cannot inspect existing destination %s: %w", cleanDest, readErr)
		}
		if len(entries) > 0 {
			return fmt.Errorf("destination directory is not empty; refusing overwrite: %s", cleanDest)
		}
	} else if !os.IsNotExist(err) {
		return err
	}

	// Staging dir under parentDir guarantees same-filesystem atomic rename.
	stageRoot, err := os.MkdirTemp(parentDir, ".labops-deliver-*")
	if err != nil {
		return fmt.Errorf("cannot create delivery staging dir: %w", err)
	}
	defer os.RemoveAll(stageRoot)
	_ = os.Chmod(stageRoot, 0700)

	// 1. Copy artifact to private temp file while verifying SHA-256 to eliminate inode TOCTOU.
	verifiedTarCopy, _, err := CopyAndVerifySHA256(artifactPath, stageRoot, expectedDigest, MaxRepoSizeBytes)
	if err != nil {
		return err
	}

	extractDir := filepath.Join(stageRoot, "extracted")
	if err := os.MkdirAll(extractDir, 0750); err != nil {
		return err
	}

	if err := extractVerifiedTarGz(verifiedTarCopy, extractDir); err != nil {
		return err
	}

	finalStageDir := extractDir

	// 2. If .labops_bundle is present (or expectedCommitSHA is enforced), verify bundle & materialize clean Git repo.
	bundleMetaDir := filepath.Join(extractDir, BundleSubdirName)
	_, metaStatErr := os.Lstat(bundleMetaDir)
	hasBundleMeta := metaStatErr == nil

	if hasBundleMeta || expectedCommitSHA != "" {
		manifest, bundlePath, err := VerifyBundleMetadata(extractDir, expectedCommitSHA)
		if err != nil {
			return fmt.Errorf("git bundle verification failed: %w", err)
		}

		ctxTimeout, cancel := context.WithTimeout(ctx, 2*time.Minute)
		defer cancel()

		repoStageDir := filepath.Join(stageRoot, "repo")
		if err := MaterializeGitWorktreeFromBundle(ctxTimeout, bundlePath, manifest, repoStageDir, MaxRepoSizeBytes, MaxFileCount); err != nil {
			return fmt.Errorf("failed materializing git working tree from bundle: %w", err)
		}
		finalStageDir = repoStageDir
	}

	// 3. Re-check destination symlink safety immediately before atomic rename.
	if err := LstatNoSymlinkChainFromRoot(cleanDest); err != nil {
		return fmt.Errorf("destination symlink race detected before commit: %w", err)
	}
	if err := os.Rename(finalStageDir, cleanDest); err != nil {
		return fmt.Errorf("atomic workspace delivery rename failed: %w", err)
	}
	return nil
}

func extractVerifiedTarGz(tarGzPath, extractDir string) error {
	f, err := os.Open(tarGzPath)
	if err != nil {
		return err
	}
	defer f.Close()

	gr, err := gzip.NewReader(f)
	if err != nil {
		return fmt.Errorf("corrupt gzip artifact: %w", err)
	}
	defer gr.Close()

	tr := tar.NewReader(gr)
	var totalBytes int64
	var fileCount int

	for {
		header, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return fmt.Errorf("corrupt tar stream during delivery: %w", err)
		}

		if header.Typeflag == tar.TypeXHeader || header.Typeflag == tar.TypeXGlobalHeader {
			continue
		}

		cleanRel, err := ValidateArchivePath(header.Name)
		if err != nil {
			return fmt.Errorf("unsafe delivery archive path (%s): %w", header.Name, err)
		}
		if cleanRel == "." {
			continue
		}

		target := filepath.Join(extractDir, filepath.FromSlash(cleanRel))
		if err := LstatNoSymlinkChain(extractDir, target); err != nil {
			return fmt.Errorf("symlink traversal detected during delivery (%s): %w", header.Name, err)
		}

		fileCount++
		if fileCount > MaxFileCount {
			return fmt.Errorf("delivery file count exceeds limit (%d)", MaxFileCount)
		}

		switch header.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0750); err != nil {
				return err
			}

		case tar.TypeReg, tar.TypeRegA:
			if header.Size < 0 {
				return fmt.Errorf("invalid negative archive file size: %s", header.Name)
			}
			remaining := MaxRepoSizeBytes - totalBytes
			if remaining < 0 || header.Size > remaining {
				return fmt.Errorf("delivery extracted bytes exceed limit (%d MB)", MaxRepoSizeBytes/(1024*1024))
			}

			if err := os.MkdirAll(filepath.Dir(target), 0750); err != nil {
				return err
			}

			outFile, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0640)
			if err != nil {
				return err
			}
			n, copyErr := io.Copy(outFile, io.LimitReader(tr, remaining+1))
			closeErr := outFile.Close()
			if copyErr != nil {
				return copyErr
			}
			if closeErr != nil {
				return closeErr
			}
			totalBytes += n
			if totalBytes > MaxRepoSizeBytes {
				return fmt.Errorf("delivery bomb detected: extracted bytes exceed limit (%d MB)", MaxRepoSizeBytes/(1024*1024))
			}

		case tar.TypeSymlink:
			if err := ValidateArchiveSymlink(extractDir, target, header.Linkname); err != nil {
				return fmt.Errorf("unsafe symlink in delivery archive (%s -> %s): %w", header.Name, header.Linkname, err)
			}
			if err := os.MkdirAll(filepath.Dir(target), 0750); err != nil {
				return err
			}
			_ = os.Remove(target)
			if err := os.Symlink(header.Linkname, target); err != nil {
				return err
			}

		default:
			return fmt.Errorf("disallowed tar entry type (%d) in delivery archive: %s", header.Typeflag, header.Name)
		}
	}

	return nil
}
