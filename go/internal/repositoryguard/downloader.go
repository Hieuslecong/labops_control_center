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
	BundleSHA256 string
	TotalSize    int64
	FileCount    int
}

// PrepareQuarantine tải snapshot kho mã tại đúng commit_sha vào thư mục cách ly độc lập.
func PrepareQuarantine(ctx context.Context, baseQuarantineDir, requestID, repoURL, commitSHA string) (*QuarantineWorkspace, error) {
	return PrepareQuarantineWithCanonical(ctx, baseQuarantineDir, requestID, repoURL, repoURL, commitSHA)
}

func extractTarToQuarantine(archiveCmd *exec.Cmd, tarOut io.Reader, sourceDir string) (int64, int, error) {
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
			return 0, 0, fmt.Errorf("lỗi đọc archive: %w", err)
		}

		if header.Typeflag == tar.TypeXHeader || header.Typeflag == tar.TypeXGlobalHeader {
			continue
		}

		cleanName, err := ValidateArchivePath(header.Name)
		if err != nil {
			_ = archiveCmd.Process.Kill()
			_ = archiveCmd.Wait()
			return 0, 0, fmt.Errorf("phát hiện đường dẫn tệp độc hại (%s): %w", header.Name, err)
		}
		if cleanName == "." {
			continue
		}

		targetPath := filepath.Join(sourceDir, filepath.FromSlash(cleanName))
		if err := LstatNoSymlinkChain(sourceDir, targetPath); err != nil {
			_ = archiveCmd.Process.Kill()
			_ = archiveCmd.Wait()
			return 0, 0, fmt.Errorf("phát hiện chuỗi symlink nguy hiểm trong quarantine (%s): %w", header.Name, err)
		}

		fileCount++
		if fileCount > MaxFileCount {
			_ = archiveCmd.Process.Kill()
			_ = archiveCmd.Wait()
			return 0, 0, fmt.Errorf("vượt giới hạn số lượng tệp tối đa (%d tệp)", MaxFileCount)
		}

		switch header.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(targetPath, 0750); err != nil {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return 0, 0, err
			}
		case tar.TypeReg, tar.TypeRegA:
			if header.Size < 0 {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return 0, 0, fmt.Errorf("kích thước tệp âm không hợp lệ: %s", header.Name)
			}
			remaining := MaxRepoSizeBytes - totalSize
			if remaining < 0 || header.Size > remaining {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return 0, 0, fmt.Errorf("kích thước kho mã vượt giới hạn tối đa (%d MB)", MaxRepoSizeBytes/(1024*1024))
			}

			if err := os.MkdirAll(filepath.Dir(targetPath), 0750); err != nil {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return 0, 0, err
			}

			outFile, err := os.OpenFile(targetPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0640)
			if err != nil {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return 0, 0, err
			}

			n, copyErr := io.Copy(outFile, io.LimitReader(tr, remaining+1))
			closeErr := outFile.Close()
			if copyErr != nil {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return 0, 0, copyErr
			}
			if closeErr != nil {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return 0, 0, closeErr
			}
			totalSize += n
			if totalSize > MaxRepoSizeBytes {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return 0, 0, fmt.Errorf("kích thước kho mã vượt giới hạn tối đa (%d MB)", MaxRepoSizeBytes/(1024*1024))
			}

		case tar.TypeSymlink:
			if err := ValidateArchiveSymlink(sourceDir, targetPath, header.Linkname); err != nil {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return 0, 0, fmt.Errorf("phát hiện symlink traversal attack (%s -> %s): %w", header.Name, header.Linkname, err)
			}
			if err := os.MkdirAll(filepath.Dir(targetPath), 0750); err != nil {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return 0, 0, err
			}
			if err := os.Symlink(header.Linkname, targetPath); err != nil {
				_ = archiveCmd.Process.Kill()
				_ = archiveCmd.Wait()
				return 0, 0, err
			}

		default:
			_ = archiveCmd.Process.Kill()
			_ = archiveCmd.Wait()
			return 0, 0, fmt.Errorf("loại entry tar không được phép (%d): %s", header.Typeflag, header.Name)
		}
	}

	if err := archiveCmd.Wait(); err != nil {
		return 0, 0, fmt.Errorf("git archive trả về lỗi: %w", err)
	}
	return totalSize, fileCount, nil
}

// PrepareQuarantineWithCanonical tải snapshot, kiểm tra ràng buộc đường dẫn/symlink và đóng gói tar.gz kèm .labops_bundle.
func PrepareQuarantineWithCanonical(ctx context.Context, baseQuarantineDir, requestID, repoURL, canonicalRepo, commitSHA string) (*QuarantineWorkspace, error) {
	reqDir := filepath.Join(baseQuarantineDir, requestID)
	sourceDir := filepath.Join(reqDir, "source")
	archivePath := filepath.Join(reqDir, "source.tar.gz")

	if err := LstatNoSymlinkChain(baseQuarantineDir, reqDir); err != nil {
		return nil, fmt.Errorf("đường dẫn quarantine không an toàn: %w", err)
	}
	if err := os.Mkdir(reqDir, 0750); err != nil {
		return nil, fmt.Errorf("không thể tạo thư mục quarantine: %w", err)
	}
	prepared := false
	defer func() {
		if !prepared {
			_ = os.RemoveAll(reqDir)
		}
	}()
	if err := os.Mkdir(sourceDir, 0750); err != nil {
		return nil, fmt.Errorf("không thể tạo thư mục source: %w", err)
	}

	ctxTimeout, cancel := context.WithTimeout(ctx, 3*time.Minute)
	defer cancel()

	// Fetch the resolved commit and its complete reachable history.
	gitDir := filepath.Join(reqDir, ".git_clone_tmp")
	defer os.RemoveAll(gitDir)
	if _, err := runGit(ctxTimeout, "", "init", gitDir); err != nil {
		return nil, err
	}
	if _, err := runGit(ctxTimeout, gitDir, "fetch", "--no-tags", "--", repoURL, commitSHA); err != nil {
		return nil, fmt.Errorf("không thể fetch commit đã ghim: %w", err)
	}

	// Ensure the checkout and the scanner snapshot contain the same tracked files.
	attributes := filepath.Join(gitDir, ".git", "info", "attributes")
	if err := os.WriteFile(attributes, []byte("* -export-ignore -export-subst\n"), 0600); err != nil {
		return nil, err
	}

	safeArgs := SafeGitConfigArgs()
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
	totalSize, fileCount, err := extractTarToQuarantine(archiveCmd, tarOut, sourceDir)
	if err != nil {
		return nil, err
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

	prepared = true
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

// copyGitMetadataDir sao chép chỉ đọc thư mục .git bằng quyền của tiến trình hiện tại (CAP_DAC_READ_SEARCH),
// bỏ qua hooks và symlinks để các tiến trình con git không bị kernel chặn access().
func copyGitMetadataDir(srcGitDir, dstGitDir string, maxBytes int64) error {
	if err := os.MkdirAll(dstGitDir, 0700); err != nil {
		return err
	}
	var copiedBytes int64
	var walkCopy func(src, dst string, isTopLevel bool) error
	walkCopy = func(src, dst string, isTopLevel bool) error {
		entries, err := os.ReadDir(src)
		if err != nil {
			return err
		}
		for _, ent := range entries {
			name := ent.Name()
			if isTopLevel && name == "hooks" {
				continue
			}
			srcPath := filepath.Join(src, name)
			dstPath := filepath.Join(dst, name)
			info, err := os.Lstat(srcPath)
			if err != nil {
				return err
			}
			if info.Mode()&os.ModeSymlink != 0 {
				continue
			}
			if info.IsDir() {
				if err := os.MkdirAll(dstPath, 0700); err != nil {
					return err
				}
				if err := walkCopy(srcPath, dstPath, false); err != nil {
					return err
				}
				continue
			}
			if !info.Mode().IsRegular() {
				continue
			}
			if info.Size() > maxBytes-copiedBytes {
				return fmt.Errorf("kích thước dữ liệu .git vượt giới hạn tối đa (%d MB)", maxBytes/(1024*1024))
			}
			in, err := os.Open(srcPath)
			if err != nil {
				return err
			}
			out, err := os.OpenFile(dstPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0600)
			if err != nil {
				_ = in.Close()
				return err
			}
			n, copyErr := io.Copy(out, io.LimitReader(in, (maxBytes-copiedBytes)+1))
			closeInErr := in.Close()
			closeOutErr := out.Close()
			if copyErr != nil {
				return copyErr
			}
			if closeInErr != nil {
				return closeInErr
			}
			if closeOutErr != nil {
				return closeOutErr
			}
			copiedBytes += n
			if copiedBytes > maxBytes {
				return fmt.Errorf("kích thước dữ liệu .git vượt giới hạn tối đa (%d MB)", maxBytes/(1024*1024))
			}
		}
		return nil
	}
	return walkCopy(srcGitDir, dstGitDir, true)
}

// PrepareQuarantineFromLocalGit trích xuất chỉ đọc snapshot tại commitSHA từ repo đã clone sẵn trong workspace host.
func PrepareQuarantineFromLocalGit(ctx context.Context, baseQuarantineDir, requestID, workspaceRoot, relRepoPath, canonicalRepo, commitSHA string) (*QuarantineWorkspace, error) {
	cleanRoot := filepath.Clean(strings.TrimSpace(workspaceRoot))
	if !filepath.IsAbs(cleanRoot) {
		return nil, errors.New("workspaceRoot phải là đường dẫn tuyệt đối")
	}
	rel := strings.TrimSpace(relRepoPath)
	if rel == "" || rel == "." || strings.ContainsAny(rel, "\\\x00") || !filepath.IsLocal(rel) {
		return nil, fmt.Errorf("đường dẫn repo trong workspace không hợp lệ: %q", relRepoPath)
	}
	for _, seg := range strings.Split(filepath.ToSlash(rel), "/") {
		if seg == ".." || seg == ".ssh" || seg == ".git" {
			return nil, fmt.Errorf("đường dẫn repo chứa thành phần bị cấm: %q", relRepoPath)
		}
	}

	hostRepoDir := filepath.Join(cleanRoot, filepath.Clean(rel))
	if err := LstatNoSymlinkChain(cleanRoot, hostRepoDir); err != nil {
		return nil, fmt.Errorf("đường dẫn repo trong workspace không an toàn: %w", err)
	}
	repoStat, err := os.Lstat(hostRepoDir)
	if err != nil || !repoStat.IsDir() {
		return nil, fmt.Errorf("không tìm thấy thư mục repo trong workspace: %s", rel)
	}
	gitMetaPath := filepath.Join(hostRepoDir, ".git")
	if err := LstatNoSymlinkChain(cleanRoot, gitMetaPath); err != nil {
		return nil, fmt.Errorf("thư mục .git trong workspace không an toàn: %w", err)
	}
	if _, err := os.Lstat(gitMetaPath); err != nil {
		return nil, fmt.Errorf("thư mục workspace không phải Git repository: %w", err)
	}

	reqDir := filepath.Join(baseQuarantineDir, requestID)
	sourceDir := filepath.Join(reqDir, "source")
	archivePath := filepath.Join(reqDir, "source.tar.gz")

	if err := LstatNoSymlinkChain(baseQuarantineDir, reqDir); err != nil {
		return nil, fmt.Errorf("đường dẫn quarantine không an toàn: %w", err)
	}
	if err := os.Mkdir(reqDir, 0750); err != nil {
		return nil, fmt.Errorf("không thể tạo thư mục quarantine: %w", err)
	}
	prepared := false
	defer func() {
		if !prepared {
			_ = os.RemoveAll(reqDir)
		}
	}()
	if err := os.Mkdir(sourceDir, 0750); err != nil {
		return nil, fmt.Errorf("không thể tạo thư mục source: %w", err)
	}

	ctxTimeout, cancel := context.WithTimeout(ctx, 3*time.Minute)
	defer cancel()

	// Copy .git metadata using CAP_DAC_READ_SEARCH in-process so git subprocesses never fail UID access() checks on 0700 container dirs.
	gitMetaTmp := filepath.Join(reqDir, ".git_meta")
	defer os.RemoveAll(gitMetaTmp)
	if err := copyGitMetadataDir(gitMetaPath, gitMetaTmp, MaxRepoSizeBytes); err != nil {
		return nil, fmt.Errorf("không thể đọc dữ liệu .git từ workspace: %w", err)
	}

	scopedArgs := []string{"-c", "safe.directory=" + gitMetaTmp, "--git-dir=" + gitMetaTmp}
	verifyArgs := append(append([]string{}, scopedArgs...), "rev-parse", "--verify", commitSHA+"^{commit}")
	if _, err := runGit(ctxTimeout, "", verifyArgs...); err != nil {
		return nil, fmt.Errorf("không thể xác minh commit %s trong repo workspace: %w", commitSHA, err)
	}

	archiveArgs := append(SafeGitConfigArgs(), scopedArgs...)
	archiveArgs = append(archiveArgs, "archive", "--format=tar", commitSHA)
	archiveCmd := exec.CommandContext(ctxTimeout, "git", archiveArgs...)
	archiveCmd.Env = SafeGitEnv()
	tarOut, err := archiveCmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("lỗi khởi tạo luồng git archive nội bộ: %w", err)
	}
	if err := archiveCmd.Start(); err != nil {
		return nil, fmt.Errorf("không thể trích xuất commit %s từ workspace: %w", commitSHA, err)
	}

	totalSize, fileCount, err := extractTarToQuarantine(archiveCmd, tarOut, sourceDir)
	if err != nil {
		return nil, err
	}

	if err := CreateSealedTarGz(sourceDir, "", archivePath, MaxRepoSizeBytes); err != nil {
		return nil, fmt.Errorf("không thể tạo gói snapshot quarantine: %w", err)
	}
	digest, err := HashFileSHA256(archivePath)
	if err != nil {
		return nil, fmt.Errorf("lỗi tính checksum snapshot: %w", err)
	}

	prepared = true
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
