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
	commit, _, err := resolveCheckoutRef(ctx, repoURL, ref)
	return commit, err
}

// resolveCheckoutRef returns the exact commit and the remote branch/tag used for checkout.
func resolveCheckoutRef(ctx context.Context, repoURL, ref string) (string, string, error) {
	ref = strings.TrimSpace(ref)
	if sha40Regex.MatchString(ref) {
		return strings.ToLower(ref), "", nil
	}
	if ref == "" {
		ref = "HEAD"
	}
	var candidates []string
	switch {
	case ref == "HEAD":
		candidates = []string{"HEAD"}
	case strings.HasPrefix(ref, "refs/heads/"):
		candidates = []string{ref}
	case strings.HasPrefix(ref, "refs/tags/"):
		candidates = []string{ref + "^{}", ref}
	default:
		candidates = []string{"refs/heads/" + ref, "refs/tags/" + ref + "^{}", "refs/tags/" + ref}
	}

	ctxTimeout, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	args := append(SafeGitConfigArgs(), "ls-remote", "--symref", "--", repoURL)
	args = append(args, candidates...)
	cmd := exec.CommandContext(ctxTimeout, "git", args...)
	cmd.Env = SafeGitEnv()
	out, err := cmd.Output()
	if err != nil {
		return "", "", fmt.Errorf("không thể phân giải ref %q: %w", ref, err)
	}
	refs := make(map[string]string)
	headRef := ""
	for _, line := range strings.Split(string(out), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 3 && fields[0] == "ref:" && fields[2] == "HEAD" && strings.HasPrefix(fields[1], "refs/heads/") {
			headRef = fields[1]
		}
		if len(fields) == 2 && sha40Regex.MatchString(fields[0]) {
			refs[fields[1]] = strings.ToLower(fields[0])
		}
	}
	for _, candidate := range candidates {
		if commit := refs[candidate]; commit != "" {
			if candidate == "HEAD" {
				return commit, headRef, nil
			}
			return commit, strings.TrimSuffix(candidate, "^{}"), nil
		}
	}
	return "", "", fmt.Errorf("không tìm thấy commit tương ứng với ref %q", ref)
}
