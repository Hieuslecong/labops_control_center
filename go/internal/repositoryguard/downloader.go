package repositoryguard

import (
	"archive/tar"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

const (
	MaxRepoSizeBytes int64 = 500 * 1024 * 1024 // 500 MB
	MaxFileCount     int   = 30000             // Tối đa 30.000 tệp
)

type QuarantineWorkspace struct {
	BaseDir      string
	SourceDir    string
	ArchiveFile  string
	DigestSHA256 string
	BundleSHA256 string
	TotalSize    int64
	FileCount    int
}

// PrepareQuarantine tải snapshot kho mã tại đúng commit_sha vào thư mục cách ly độc lập.
func PrepareQuarantine(ctx context.Context, baseQuarantineDir, requestID, repoURL, commitSHA string) (*QuarantineWorkspace, error) {
	return PrepareQuarantineWithCanonical(ctx, baseQuarantineDir, requestID, repoURL, repoURL, commitSHA)
}

// PrepareQuarantineWithCanonical tải snapshot, kiểm tra ràng buộc đường dẫn/symlink và đóng gói tar.gz kèm .labops_bundle.
func PrepareQuarantineWithCanonical(ctx context.Context, baseQuarantineDir, requestID, repoURL, canonicalRepo, commitSHA string) (*QuarantineWorkspace, error) {
	reqDir := filepath.Join(baseQuarantineDir, requestID)
	sourceDir := filepath.Join(reqDir, "source")
	archivePath := filepath.Join(reqDir, "source.tar.gz")

	if err := LstatNoSymlinkChain(baseQuarantineDir, reqDir); err != nil {
		return nil, fmt.Errorf("đường dẫn quarantine không an toàn: %w", err)
	}
	if err := os.MkdirAll(sourceDir, 0750); err != nil {
		return nil, fmt.Errorf("không thể tạo thư mục quarantine: %w", err)
	}

	ctxTimeout, cancel := context.WithTimeout(ctx, 3*time.Minute)
	defer cancel()

	// 1. Tải bằng git clone --no-checkout vào thư mục tạm có kiểm soát, chặn tuyệt đối hooks/filters/submodules/file://
	gitDir := filepath.Join(reqDir, ".git_clone_tmp")
	defer os.RemoveAll(gitDir)

	safeArgs := SafeGitConfigArgs()
	cloneArgs1 := append(append([]string{}, safeArgs...), "clone", "--depth=1", "--filter=blob:none", "--no-checkout", repoURL, gitDir)
	cloneCmd := exec.CommandContext(ctxTimeout, "git", cloneArgs1...)
	cloneCmd.Env = SafeGitEnv()
	if err := cloneCmd.Run(); err != nil {
		_ = os.RemoveAll(gitDir)
		cloneArgs2 := append(append([]string{}, safeArgs...), "clone", "--no-checkout", repoURL, gitDir)
		cloneFallback := exec.CommandContext(ctxTimeout, "git", cloneArgs2...)
		cloneFallback.Env = SafeGitEnv()
		if errFb := cloneFallback.Run(); errFb != nil {
			return nil, fmt.Errorf("không thể clone repo về quarantine: %w", errFb)
		}
	}

	// Fetch commit chính xác nếu cần (bỏ qua lỗi nếu commit đã nằm trong clone)
	fetchArgs := append(append([]string{}, safeArgs...), "-C", gitDir, "fetch", "--depth=1", "origin", commitSHA)
	fetchCmd := exec.CommandContext(ctxTimeout, "git", fetchArgs...)
	fetchCmd.Env = SafeGitEnv()
	_ = fetchCmd.Run()

	// Xác minh tuyệt đối commitSHA tồn tại trong object database
	if _, err := runGit(ctxTimeout, gitDir, "rev-parse", "--verify", commitSHA+"^{commit}"); err != nil {
		return nil, fmt.Errorf("không thể xác minh commit %s trong repo: %w", commitSHA, err)
	}

	// Checkout commit SHA chính xác vào sourceDir thông qua git archive (không kích hoạt hooks)
	archiveArgs := append(append([]string{}, safeArgs...), "-C", gitDir, "archive", "--format=tar", commitSHA)
	archiveCmd := exec.CommandContext(ctxTimeout, "git", archiveArgs...)
	archiveCmd.Env = SafeGitEnv()
	tarOut, err := archiveCmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("lỗi khởi tạo luồng git archive: %w", err)
	}

	if err := archiveCmd.Start(); err != nil {
		return nil, fmt.Errorf("không thể trích xuất commit %s từ git archive: %w", commitSHA, err)
	}

	// 2. Bung tar vào sourceDir kèm kiểm tra bảo mật nghiêm ngặt (Path Depth, LimitReader Bomb, Symlink Chain)
	tr := tar.NewReader(tarOut)
	var totalSize int64
	var fileCount int

	for {
		header, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			_ = archiveCmd.Process.Kill()
			_ = archiveCmd.Wait()
			return nil, fmt.Errorf("lỗi đọc archive: %w", err)
		}

		if header.Typeflag == tar.TypeXHeader || header.Typeflag == tar.TypeXGlobalHeader {
			continue
		}

		cleanName, err := ValidateArchivePath(header.Name)
		if err != nil {
			_ = archiveCmd.Process.Kill()
			_ = archiveCmd.Wait()
			return nil, fmt.Errorf("phát hiện đường dẫn tệp độc hại (%s): %w", header.Name, err)
		}
		if cleanName == "." {
			continue
		}

		targetPath := filepath.Join(sourceDir, filepath.FromSlash(cleanName))
		if err := LstatNoSymlinkChain(sourceDir, targetPath); err != nil {
			_ = archiveCmd.Process.Kill()
			_ = archiveCmd.Wait()
			return nil, fmt.Errorf("phát hiện chuỗi symlink nguy hiểm trong quarantine (%s): %w", header.Name, err)
		}

		fileCount++
		if fileCount > MaxFileCount {
			_ = archiveCmd.Process.Kill()
			_ = archiveCmd.Wait()
			return nil, fmt.Errorf("vượt giới hạn số lượng tệp tối đa (%d tệp)", MaxFileCount)
		}

		switch header.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(targetPath, 0750); err != nil {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return nil, err
			}
		case tar.TypeReg, tar.TypeRegA:
			if header.Size < 0 {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return nil, fmt.Errorf("kích thước tệp âm không hợp lệ: %s", header.Name)
			}
			remaining := MaxRepoSizeBytes - totalSize
			if remaining < 0 || header.Size > remaining {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return nil, fmt.Errorf("kích thước kho mã vượt giới hạn tối đa (%d MB)", MaxRepoSizeBytes/(1024*1024))
			}

			if err := os.MkdirAll(filepath.Dir(targetPath), 0750); err != nil {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return nil, err
			}

			outFile, err := os.OpenFile(targetPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0640)
			if err != nil {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return nil, err
			}

			n, copyErr := io.Copy(outFile, io.LimitReader(tr, remaining+1))
			closeErr := outFile.Close()
			if copyErr != nil {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return nil, copyErr
			}
			if closeErr != nil {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return nil, closeErr
			}
			totalSize += n
			if totalSize > MaxRepoSizeBytes {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return nil, fmt.Errorf("kích thước kho mã vượt giới hạn tối đa (%d MB)", MaxRepoSizeBytes/(1024*1024))
			}

		case tar.TypeSymlink:
			if err := ValidateArchiveSymlink(sourceDir, targetPath, header.Linkname); err != nil {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return nil, fmt.Errorf("phát hiện symlink traversal attack (%s -> %s): %w", header.Name, header.Linkname, err)
			}
			if err := os.MkdirAll(filepath.Dir(targetPath), 0750); err != nil {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return nil, err
			}
			if err := os.Symlink(header.Linkname, targetPath); err != nil {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return nil, err
			}

		default:
			_ = archiveCmd.Process.Kill()
			_ = archiveCmd.Wait()
			return nil, fmt.Errorf("loại entry tar không được phép (%d): %s", header.Typeflag, header.Name)
		}
	}

	if err := archiveCmd.Wait(); err != nil {
		return nil, fmt.Errorf("git archive trả về lỗi: %w", err)
	}

	// 3. Tạo Git bundle + manifest trong thư mục staging riêng để không lẫn vào bước quét sourceDir
	bundleStageDir := filepath.Join(reqDir, ".bundle_stage")
	defer os.RemoveAll(bundleStageDir)
	manifest, err := BuildGitBundleAndManifest(ctxTimeout, gitDir, bundleStageDir, repoURL, canonicalRepo, commitSHA, CurrentPolicyVersion)
	if err != nil {
		return nil, fmt.Errorf("không thể đóng gói git bundle bảo mật: %w", err)
	}

	// 4. Đóng gói sourceDir + .labops_bundle thành file archive .tar.gz chế độ chỉ đọc 0440
	if err := CreateSealedTarGz(sourceDir, bundleStageDir, archivePath, MaxRepoSizeBytes); err != nil {
		return nil, fmt.Errorf("không thể tạo gói phân phối tar.gz: %w", err)
	}

	// Tính digest SHA-256 chính xác của file archive .tar.gz
	digest, err := HashFileSHA256(archivePath)
	if err != nil {
		return nil, fmt.Errorf("lỗi tính checksum archive: %w", err)
	}

	return &QuarantineWorkspace{
		BaseDir:      reqDir,
		SourceDir:    sourceDir,
		ArchiveFile:  archivePath,
		DigestSHA256: digest,
		BundleSHA256: manifest.BundleSHA256,
		TotalSize:    totalSize,
		FileCount:    fileCount,
	}, nil
}

func createTarGz(srcDir, tarGzPath string) error {
	return CreateSealedTarGz(srcDir, "", tarGzPath, MaxRepoSizeBytes)
}

// Cleanup dọn dẹp thư mục mã nguồn bung thô nhưng giữ lại source.tar.gz phục vụ Admin review
func (qw *QuarantineWorkspace) Cleanup() error {
	if qw.SourceDir == "" {
		return nil
	}
	return os.RemoveAll(qw.SourceDir)
}

// PurgeAll xóa toàn bộ thư mục request khi bị REJECTED hoặc hủy bỏ
func (qw *QuarantineWorkspace) PurgeAll() error {
	if qw.BaseDir == "" || qw.BaseDir == "/" {
		return errors.New("đường dẫn BaseDir không hợp lệ để cleanup")
	}
	return os.RemoveAll(qw.BaseDir)
}
