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
	"os/exec"
	"path/filepath"
	"strings"
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
	TotalSize    int64
	FileCount    int
}

// PrepareQuarantine tải snapshot kho mã tại đúng commit_sha vào thư mục cách ly độc lập
func PrepareQuarantine(ctx context.Context, baseQuarantineDir, requestID, repoURL, commitSHA string) (*QuarantineWorkspace, error) {
	reqDir := filepath.Join(baseQuarantineDir, requestID)
	sourceDir := filepath.Join(reqDir, "source")
	archivePath := filepath.Join(reqDir, "source.tar.gz")

	if err := os.MkdirAll(sourceDir, 0750); err != nil {
		return nil, fmt.Errorf("không thể tạo thư mục quarantine: %w", err)
	}

	ctxTimeout, cancel := context.WithTimeout(ctx, 3*time.Minute)
	defer cancel()

	// 1. Tải bằng git clone nông (shallow clone 1 commit) vào thư mục tạm có kiểm soát
	// Sử dụng git config cấm thực thi filters và hooks
	gitDir := filepath.Join(reqDir, ".git_clone_tmp")
	defer os.RemoveAll(gitDir)

	cloneCmd := exec.CommandContext(ctxTimeout, "git", "clone",
		"--depth=1",
		"--filter=blob:none",
		"--no-checkout",
		"-c", "core.hooksPath=/dev/null",
		repoURL, gitDir)
	if err := cloneCmd.Run(); err != nil {
		// Thử clone truyền thống với depth 1 nếu filter blob không hỗ trợ
		cloneFallback := exec.CommandContext(ctxTimeout, "git", "clone",
			"--depth=1",
			"-c", "core.hooksPath=/dev/null",
			repoURL, gitDir)
		if errFb := cloneFallback.Run(); errFb != nil {
			return nil, fmt.Errorf("không thể clone repo về quarantine: %w", errFb)
		}
	}

	// Fetch commit chính xác
	fetchCmd := exec.CommandContext(ctxTimeout, "git", "-C", gitDir, "fetch", "--depth=1", "origin", commitSHA)
	_ = fetchCmd.Run() // Bỏ qua nếu commit đã nằm trong shallow clone

	// Checkout commit SHA chính xác vào sourceDir mà không kích hoạt git hooks
	archiveCmd := exec.CommandContext(ctxTimeout, "git", "-C", gitDir, "archive", "--format=tar", commitSHA)
	tarOut, err := archiveCmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("lỗi khởi tạo luồng git archive: %w", err)
	}

	if err := archiveCmd.Start(); err != nil {
		return nil, fmt.Errorf("không thể trích xuất commit %s từ git archive: %w", commitSHA, err)
	}

	// 2. Bung tar vào sourceDir kèm kiểm tra bảo mật (Zip Bomb, Symlink Traversal)
	tr := tar.NewReader(tarOut)
	hasher := sha256.New()
	var totalSize int64
	var fileCount int

	for {
		header, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("lỗi đọc archive: %w", err)
		}

		// Kiểm tra Path Traversal
		cleanName := filepath.Clean(header.Name)
		if strings.HasPrefix(cleanName, "../") || strings.HasPrefix(cleanName, "/") {
			return nil, fmt.Errorf("phát hiện đường dẫn tệp độc hại (Path Traversal): %s", header.Name)
		}

		targetPath := filepath.Join(sourceDir, cleanName)
		rel, err := filepath.Rel(sourceDir, targetPath)
		if err != nil || strings.HasPrefix(rel, "..") {
			return nil, fmt.Errorf("tệp nằm ngoài vùng quarantine: %s", header.Name)
		}

		fileCount++
		if fileCount > MaxFileCount {
			return nil, fmt.Errorf("vượt giới hạn số lượng tệp tối đa (%d tệp)", MaxFileCount)
		}

		switch header.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(targetPath, 0750); err != nil {
				return nil, err
			}
		case tar.TypeReg, tar.TypeRegA:
			totalSize += header.Size
			if totalSize > MaxRepoSizeBytes {
				return nil, fmt.Errorf("kích thước kho mã vượt giới hạn tối đa (%d MB)", MaxRepoSizeBytes/(1024*1024))
			}

			if err := os.MkdirAll(filepath.Dir(targetPath), 0750); err != nil {
				return nil, err
			}

			outFile, err := os.OpenFile(targetPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0640)
			if err != nil {
				return nil, err
			}

			tee := io.TeeReader(tr, hasher)
			if _, err := io.Copy(outFile, tee); err != nil {
				outFile.Close()
				return nil, err
			}
			outFile.Close()

		case tar.TypeSymlink:
			// Kiểm tra Symlink Traversal: đích trỏ của symlink không được ra ngoài sourceDir
			linkTarget := header.Linkname
			resolvedDest := filepath.Clean(filepath.Join(filepath.Dir(targetPath), linkTarget))
			relDest, err := filepath.Rel(sourceDir, resolvedDest)
			if err != nil || strings.HasPrefix(relDest, "..") {
				return nil, fmt.Errorf("phát hiện symlink trỏ ra ngoài quarantine (Symlink Traversal Attack): %s -> %s", header.Name, linkTarget)
			}
			// Tạo symlink an toàn
			if err := os.Symlink(linkTarget, targetPath); err != nil {
				return nil, err
			}
		}
	}

	if err := archiveCmd.Wait(); err != nil {
		return nil, fmt.Errorf("git archive trả về lỗi: %w", err)
	}

	digest := hex.EncodeToString(hasher.Sum(nil))

	// 3. Nén thành file archive .tar.gz có chữ ký digest để cache và delivery
	if err := createTarGz(sourceDir, archivePath); err != nil {
		return nil, fmt.Errorf("không thể tạo gói phân phối tar.gz: %w", err)
	}

	return &QuarantineWorkspace{
		BaseDir:      reqDir,
		SourceDir:    sourceDir,
		ArchiveFile:  archivePath,
		DigestSHA256: digest,
		TotalSize:    totalSize,
		FileCount:    fileCount,
	}, nil
}

func createTarGz(srcDir, tarGzPath string) error {
	outFile, err := os.Create(tarGzPath)
	if err != nil {
		return err
	}
	defer outFile.Close()

	gw := gzip.NewWriter(outFile)
	defer gw.Close()

	tw := tar.NewWriter(gw)
	defer tw.Close()

	return filepath.Walk(srcDir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(srcDir, path)
		if err != nil || rel == "." {
			return nil
		}

		header, err := tar.FileInfoHeader(info, info.Name())
		if err != nil {
			return err
		}
		header.Name = filepath.ToSlash(rel)

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
			defer f.Close()
			_, err = io.Copy(tw, f)
			return err
		}
		return nil
	})
}

// Cleanup dọn dẹp thư mục làm việc tạm thời
func (qw *QuarantineWorkspace) Cleanup() error {
	if qw.BaseDir == "" || qw.BaseDir == "/" {
		return errors.New("đường dẫn BaseDir không hợp lệ để cleanup")
	}
	return os.RemoveAll(qw.BaseDir)
}
