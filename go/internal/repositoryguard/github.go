package repositoryguard

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"regexp"
	"strings"
	"time"
)

var (
	sha40Regex    = regexp.MustCompile(`^[0-9a-fA-F]{40}$`)
	repoPathRegex = regexp.MustCompile(`^[a-zA-Z0-9_.\-]+/[a-zA-Z0-9_.\-]+$`)
	refNameRegex  = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$`)
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
	if u.Opaque != "" || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || u.Port() != "" && u.Port() != "443" {
		return "", "", "", errors.New("URL kho mã không được chứa query, fragment, opaque path hoặc port tùy ý")
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
	if len(parts) != 2 || !repoPathRegex.MatchString(trimmedPath) || parts[0] == "." || parts[0] == ".." || parts[1] == "." || parts[1] == ".." {
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
	if validCommitSHA(ref) {
		return strings.ToLower(ref), nil
	}
	if ref == "" {
		ref = "HEAD"
	}
	if !validRequestedRef(ref) {
		return "", errors.New("repository reference is invalid")
	}

	// Sử dụng git ls-remote để lấy commit SHA thực tế từ remote mà không cần clone
	ctxTimeout, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()

	if ref == "HEAD" {
		cmd := trustedGitCommand(ctxTimeout, "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
			"-c", "http.followRedirects=false", "ls-remote", "--symref", "--", repoURL, "HEAD")
		out, err := cmd.Output()
		if err != nil {
			return "", fmt.Errorf("cannot resolve repository HEAD: %w", err)
		}
		for _, line := range strings.Split(string(out), "\n") {
			fields := strings.Fields(line)
			if len(fields) == 2 && fields[1] == "HEAD" && sha40Regex.MatchString(fields[0]) {
				return strings.ToLower(fields[0]), nil
			}
		}
		return "", errors.New("repository HEAD did not resolve to a commit")
	}

	refs := []string{ref}
	if !strings.HasPrefix(ref, "refs/") {
		refs = []string{"refs/heads/" + ref, "refs/tags/" + ref, "refs/tags/" + ref + "^{}"}
	} else if strings.HasPrefix(ref, "refs/tags/") {
		refs = append(refs, ref+"^{}")
	}
	cmdArgs := append([]string{"-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "http.followRedirects=false", "ls-remote", "--", repoURL}, refs...)
	cmd := trustedGitCommand(ctxTimeout, cmdArgs...)
	out, err := cmd.Output()
	if err != nil {
		return "", fmt.Errorf("cannot resolve repository ref %q: %w", ref, err)
	}
	matches := make(map[string]string)
	for _, line := range strings.Split(string(out), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 2 && sha40Regex.MatchString(fields[0]) {
			matches[fields[1]] = strings.ToLower(fields[0])
		}
	}
	var resolved []string
	for _, candidate := range refs {
		if strings.HasSuffix(candidate, "^{}") {
			continue
		}
		if sha, ok := matches[candidate+"^{}"]; ok {
			resolved = append(resolved, sha)
		} else if sha, ok := matches[candidate]; ok {
			resolved = append(resolved, sha)
		}
	}
	if len(resolved) == 0 {
		return "", fmt.Errorf("repository reference %q was not found", ref)
	}
	for _, sha := range resolved[1:] {
		if sha != resolved[0] {
			return "", fmt.Errorf("repository reference %q is ambiguous between branch and tag", ref)
		}
	}
	return resolved[0], nil
}

func validRequestedRef(ref string) bool {
	if ref == "HEAD" {
		return true
	}
	if !refNameRegex.MatchString(ref) || strings.Contains(ref, "..") || strings.Contains(ref, "//") ||
		strings.Contains(ref, "@{") || strings.HasSuffix(ref, ".") || strings.HasSuffix(ref, ".lock") ||
		strings.HasSuffix(ref, "/") || strings.HasPrefix(ref, "/") {
		return false
	}
	for _, part := range strings.Split(ref, "/") {
		if part == "." || part == ".." || strings.HasPrefix(part, ".") || strings.HasSuffix(part, ".lock") {
			return false
		}
	}
	return true
}
