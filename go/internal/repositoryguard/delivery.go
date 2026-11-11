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
	"path/filepath"
	"strings"
)

// DeliverArtifact giải nén artifact đã được duyệt vào thư mục đích của Docker Workspace
func DeliverArtifact(artifactPath, expectedDigest, destDir string) error {
	if artifactPath == "" || destDir == "" {
		return errors.New("đường dẫn artifact hoặc destDir không được để trống")
	}

	// 1. Kiểm tra toàn vẹn Content Digest (SHA-256)
	f, err := os.Open(artifactPath)
	if err != nil {
		return fmt.Errorf("không thể mở file artifact: %w", err)
	}
	defer f.Close()

	hasher := sha256.New()
	if _, err := io.Copy(hasher, f); err != nil {
		return fmt.Errorf("lỗi tính toán checksum artifact: %w", err)
	}

	actualDigest := hex.EncodeToString(hasher.Sum(nil))
	if actualDigest != expectedDigest {
		return fmt.Errorf("TOCTOU / Integrity Failure: Checksum artifact không khớp (expected: %s, actual: %s)",
			expectedDigest, actualDigest)
	}

	// Đưa con trỏ file về đầu để đọc tar.gz
	if _, err := f.Seek(0, io.SeekStart); err != nil {
		return err
	}

	// 2. Chuẩn bị thư mục đích
	if err := os.MkdirAll(destDir, 0755); err != nil {
		return fmt.Errorf("không thể tạo thư mục đích %s: %w", destDir, err)
	}

	// 3. Giải nén an toàn
	gzr, err := gzip.NewReader(f)
	if err != nil {
		return fmt.Errorf("lỗi đọc gzip stream: %w", err)
	}
	defer gzr.Close()

	tr := tar.NewReader(gzr)
	for {
		header, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return fmt.Errorf("lỗi đọc tar entry: %w", err)
		}

		cleanPath := filepath.Clean(header.Name)
		if strings.HasPrefix(cleanPath, "..") || strings.HasPrefix(cleanPath, "/") {
			return fmt.Errorf("phát hiện đường dẫn không an toàn khi phân phối: %s", header.Name)
		}

		target := filepath.Join(destDir, cleanPath)
		rel, err := filepath.Rel(destDir, target)
		if err != nil || strings.HasPrefix(rel, "..") {
			return fmt.Errorf("tệp phân phối trỏ ra ngoài đích: %s", header.Name)
		}

		switch header.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0755); err != nil {
				return err
			}
		case tar.TypeReg, tar.TypeRegA:
			if err := os.MkdirAll(filepath.Dir(target), 0755); err != nil {
				return err
			}
			outFile, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0644)
			if err != nil {
				return err
			}
			if _, err := io.Copy(outFile, tr); err != nil {
				outFile.Close()
				return err
			}
			outFile.Close()
		case tar.TypeSymlink:
			// Symlink nội bộ
			linkTarget := header.Linkname
			resolvedDest := filepath.Clean(filepath.Join(filepath.Dir(target), linkTarget))
			relDest, err := filepath.Rel(destDir, resolvedDest)
			if err != nil || strings.HasPrefix(relDest, "..") {
				return fmt.Errorf("symlink trỏ ra ngoài thư mục đích: %s -> %s", header.Name, linkTarget)
			}
			_ = os.Remove(target) // Xóa file cũ nếu có
			if err := os.Symlink(linkTarget, target); err != nil {
				return err
			}
		}
	}

	return nil
}
