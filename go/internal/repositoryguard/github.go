package repositoryguard

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"os/exec"
	"regexp"
	"strings"
	"time"
)

var (
	sha40Regex    = regexp.MustCompile(`^[0-9a-fA-F]{40}$`)
	repoPathRegex = regexp.MustCompile(`^[a-zA-Z0-9_.\-]+/[a-zA-Z0-9_.\-]+$`)
)

// NormalizeAndValidateURL kiểm tra URL GitHub hợp lệ và chống tấn công SSRF / Injection
func NormalizeAndValidateURL(rawURL string) (canonical, owner, repo string, err error) {
	rawURL = strings.TrimSpace(rawURL)
	if rawURL == "" {
		return "", "", "", errors.New("URL kho lưu trữ không được để trống")
	}

	u, err := url.Parse(rawURL)
	if err != nil {
		return "", "", "", fmt.Errorf("URL không hợp lệ: %w", err)
	}

	// 1. Chỉ chấp nhận giao thức HTTPS
	if strings.ToLower(u.Scheme) != "https" {
		return "", "", "", errors.New("chỉ chấp nhận giao thức https:// cho kho mã bên ngoài")
	}

	// 2. Chỉ chấp nhận github.com (hoặc gitlab.com)
	host := strings.ToLower(u.Hostname())
	if host != "github.com" && host != "gitlab.com" {
		return "", "", "", fmt.Errorf("máy chủ kho mã '%s' không được hỗ trợ; chỉ chấp nhận github.com hoặc gitlab.com", host)
	}

	// 3. Không chứa credentials trong URL (user:pass@host)
	if u.User != nil {
		return "", "", "", errors.New("URL không được chứa thông tin xác thực nhúng (credentials)")
	}

	// 4. Chuẩn hóa path: loại bỏ .git và dấu gạch chéo thừa
	trimmedPath := strings.Trim(u.Path, "/")
	trimmedPath = strings.TrimSuffix(trimmedPath, ".git")

	parts := strings.Split(trimmedPath, "/")
	if len(parts) != 2 || !repoPathRegex.MatchString(trimmedPath) {
		return "", "", "", errors.New("định dạng kho mã phải có dạng 'owner/repository'")
	}

	owner = parts[0]
	repo = parts[1]
	canonical = fmt.Sprintf("https://%s/%s/%s", host, owner, repo)

	return canonical, owner, repo, nil
}

// ResolveCommitSHA xác định commit SHA 40 ký tự hex từ branch, tag hoặc commit sẵn có
func ResolveCommitSHA(ctx context.Context, repoURL, ref string) (string, error) {
	ref = strings.TrimSpace(ref)
	if sha40Regex.MatchString(ref) {
		return strings.ToLower(ref), nil
	}

	// Sử dụng git ls-remote để lấy commit SHA thực tế từ remote mà không cần clone
	ctxTimeout, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctxTimeout, "git", "ls-remote", repoURL, ref)
	out, err := cmd.Output()
	if err != nil {
		// Thử resolve HEAD nếu ref trống
		if ref == "" || ref == "HEAD" {
			cmdHead := exec.CommandContext(ctxTimeout, "git", "ls-remote", "--symref", repoURL, "HEAD")
			outHead, errHead := cmdHead.Output()
			if errHead == nil {
				lines := strings.Split(string(outHead), "\n")
				for _, l := range lines {
					fields := strings.Fields(l)
					if len(fields) >= 2 && fields[1] == "HEAD" && sha40Regex.MatchString(fields[0]) {
						return strings.ToLower(fields[0]), nil
					}
				}
			}
		}
		return "", fmt.Errorf("không thể phân giải ref '%s' từ kho %s: %w", ref, repoURL, err)
	}

	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	for _, l := range lines {
		fields := strings.Fields(l)
		if len(fields) >= 2 && sha40Regex.MatchString(fields[0]) {
			return strings.ToLower(fields[0]), nil
		}
	}

	// Nếu ref trống nhưng chưa resolve được, gọi trực tiếp với HEAD
	if ref == "" {
		cmdHead := exec.CommandContext(ctxTimeout, "git", "ls-remote", repoURL, "HEAD")
		if outHead, errHead := cmdHead.Output(); errHead == nil {
			fields := strings.Fields(string(outHead))
			if len(fields) >= 1 && sha40Regex.MatchString(fields[0]) {
				return strings.ToLower(fields[0]), nil
			}
		}
	}

	return "", fmt.Errorf("không tìm thấy commit tương ứng với reference '%s'", ref)
}
