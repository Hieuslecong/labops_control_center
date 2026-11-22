package gitaiotcli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	workspaceTokenFile = "/run/labops/credentials/repository-token"
	DefaultAPIBaseURL  = "http://127.0.0.1:8000/api/v1/repositories"
	defaultHTTPTimeout = 30 * time.Second
	defaultPollPeriod  = 1500 * time.Millisecond
	defaultMaxWait     = 5 * time.Minute
)

// Options configures CLI execution for production invocations and isolated tests.
type Options struct {
	APIBaseURL   string
	Token        string
	TokenFile    string
	HTTPClient   *http.Client
	PollInterval time.Duration
	MaxWait      time.Duration
	Stdout       io.Writer
	Stderr       io.Writer
}

type requestStatus struct {
	ID             string `json:"id"`
	RepoURL        string `json:"repo_url"`
	CommitSHA      string `json:"commit_sha"`
	Submitter      string `json:"submitted_by"`
	State          string `json:"state"`
	CacheHit       bool   `json:"cache_hit"`
	ArtifactDigest string `json:"artifact_digest"`
	Error          string `json:"error"`
	Report         *struct {
		Verdict         string   `json:"verdict"`
		SecretsCount    int      `json:"secrets_count"`
		PolicyReasons   []string `json:"policy_reasons"`
		Vulnerabilities struct {
			Critical int `json:"critical"`
			High     int `json:"high"`
		} `json:"vulnerabilities"`
	} `json:"report"`
}

// Run executes the gitaiot CLI with default environment configuration.
func Run(args []string, progName string, stdout, stderr io.Writer) int {
	return RunWithOptions(args, progName, Options{
		Stdout: stdout,
		Stderr: stderr,
	})
}

// RunWithOptions executes the CLI with injectable options and HTTP client.
func RunWithOptions(args []string, progName string, opts Options) int {
	if opts.Stdout == nil {
		opts.Stdout = os.Stdout
	}
	if opts.Stderr == nil {
		opts.Stderr = os.Stderr
	}
	if progName == "" {
		progName = "gitaiot"
	}

	if len(args) == 0 {
		printUsage(opts.Stdout, progName)
		return 1
	}

	command := args[0]
	switch command {
	case "version", "--version", "-v":
		fmt.Fprintf(opts.Stdout, "%s CLI v2.1.0 (LabOps Secure Repository Gateway Client)\n", progName)
		return 0
	case "help", "--help", "-h":
		printUsage(opts.Stdout, progName)
		return 0
	case "clone":
		return handleClone(args[1:], &opts)
	case "status":
		return handleStatus(args[1:], &opts)
	case "deliver":
		return handleDeliver(args[1:], &opts)
	case "repo":
		return handleLegacyRepo(args[1:], &opts)
	default:
		fmt.Fprintf(opts.Stderr, "Lệnh không hợp lệ: %s\n", command)
		printUsage(opts.Stderr, progName)
		return 1
	}
}

func printUsage(w io.Writer, progName string) {
	fmt.Fprintf(w, `LabOps Control Center — Repository Security Gateway CLI (%s)

Cú pháp:
  %s clone [-b <branch|ref>] [--dest <path>] [--workspace <name>] [--no-deliver] <repo_url> [dest_dir]
  %s status [request_id]
  %s deliver <request_id> <dest_dir>
  %s version

Biến môi trường:
  LABOPS_API_URL     Backend Repositories API (mặc định: %s)
  LABOPS_TOKEN       Bearer token xác thực Researcher/Admin
  LABOPS_TOKEN_FILE  Tệp chứa token (bắt buộc chmod 0600, mặc định: /run/labops/credentials/repository-token hoặc ~/.config/labops/token)
`, progName, progName, progName, progName, progName, DefaultAPIBaseURL)
}

func resolveAPIBaseURL(opts *Options) string {
	u := strings.TrimSpace(opts.APIBaseURL)
	if u == "" {
		u = strings.TrimSpace(os.Getenv("LABOPS_API_URL"))
	}
	if u == "" {
		if content, err := os.ReadFile(filepath.Join(filepath.Dir(workspaceTokenFile), "repository-api-url")); err == nil {
			u = strings.TrimSpace(string(content))
		}
	}
	if u == "" {
		return DefaultAPIBaseURL
	}
	return strings.TrimRight(u, "/")
}

// ResolveToken resolves the bearer token from explicit options, env var, or 0600-restricted token file.
// Zero hardcoded fallback tokens are permitted.
func ResolveToken(opts *Options) (string, error) {
	if opts != nil && strings.TrimSpace(opts.Token) != "" {
		return strings.TrimSpace(opts.Token), nil
	}
	if envTok := strings.TrimSpace(os.Getenv("LABOPS_TOKEN")); envTok != "" {
		return envTok, nil
	}

	tokenFile := ""
	if opts != nil && strings.TrimSpace(opts.TokenFile) != "" {
		tokenFile = strings.TrimSpace(opts.TokenFile)
	} else if envFile := strings.TrimSpace(os.Getenv("LABOPS_TOKEN_FILE")); envFile != "" {
		tokenFile = envFile
	} else if _, err := os.Stat(workspaceTokenFile); !os.IsNotExist(err) {
		tokenFile = workspaceTokenFile
	} else {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", fmt.Errorf("không tìm thấy LABOPS_TOKEN và không thể xác định thư mục home: %w", err)
		}
		tokenFile = filepath.Join(home, ".config", "labops", "token")
	}

	info, err := os.Stat(tokenFile)
	if err != nil {
		return "", fmt.Errorf("chưa cấu hình LABOPS_TOKEN và không thể đọc tệp token (%s): %w", tokenFile, err)
	}
	if !info.Mode().IsRegular() {
		return "", fmt.Errorf("tệp token không phải regular file hợp lệ: %s", tokenFile)
	}
	if info.Mode().Perm()&0077 != 0 {
		return "", fmt.Errorf("tệp token %s có quyền quá mở (%#o); yêu cầu chmod 0600 trước khi sử dụng", tokenFile, info.Mode().Perm())
	}

	raw, err := os.ReadFile(tokenFile)
	if err != nil {
		return "", fmt.Errorf("không thể đọc tệp token %s: %w", tokenFile, err)
	}
	tok := strings.TrimSpace(string(raw))
	if tok == "" {
		return "", fmt.Errorf("tệp token %s rỗng", tokenFile)
	}
	return tok, nil
}

func resolveHTTPClient(opts *Options) *http.Client {
	if opts != nil && opts.HTTPClient != nil {
		return opts.HTTPClient
	}
	return &http.Client{
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
}

func doJSONRequest(ctx context.Context, client *http.Client, token, method, targetURL string, payload interface{}) (int, []byte, error) {
	var bodyReader io.Reader
	if payload != nil {
		raw, err := json.Marshal(payload)
		if err != nil {
			return 0, nil, err
		}
		bodyReader = bytes.NewReader(raw)
	}

	req, err := http.NewRequestWithContext(ctx, method, targetURL, bodyReader)
	if err != nil {
		return 0, nil, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Accept", "application/json")
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := client.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()

	respBytes, err := io.ReadAll(io.LimitReader(resp.Body, 4*1024*1024))
	if err != nil {
		return resp.StatusCode, nil, err
	}
	return resp.StatusCode, respBytes, nil
}

func parseCloneArgs(args []string) (repoURL, ref, destDir, workspace string, noDeliver bool, err error) {
	ref = "HEAD"
	var positionals []string
	optionsEnded := false

	for i := 0; i < len(args); i++ {
		arg := args[i]
		if optionsEnded {
			positionals = append(positionals, arg)
			continue
		}
		if value, ok := strings.CutPrefix(arg, "--branch="); ok {
			if value == "" {
				return "", "", "", "", false, errors.New("thiếu giá trị cho --branch")
			}
			ref = value
			continue
		}
		switch arg {
		case "--":
			optionsEnded = true
		case "-b", "--branch", "--ref":
			if i+1 >= len(args) {
				return "", "", "", "", false, fmt.Errorf("thiếu giá trị cho tham số %s", arg)
			}
			ref = args[i+1]
			i++
		case "--dest":
			if i+1 >= len(args) {
				return "", "", "", "", false, errors.New("thiếu đường dẫn cho --dest")
			}
			destDir = args[i+1]
			i++
		case "--workspace", "-w":
			if i+1 >= len(args) {
				return "", "", "", "", false, errors.New("thiếu tên workspace cho --workspace")
			}
			workspace = args[i+1]
			i++
		case "--no-deliver":
			noDeliver = true
		default:
			if strings.HasPrefix(arg, "-") {
				return "", "", "", "", false, fmt.Errorf("tham số không hợp lệ: %s", arg)
			}
			positionals = append(positionals, arg)
		}
	}

	if len(positionals) == 0 {
		return "", "", "", "", false, errors.New("cần cung cấp URL kho mã (ví dụ: gitaiot clone https://github.com/owner/repo)")
	}
	repoURL = positionals[0]
	if len(positionals) > 1 {
		if destDir != "" {
			return "", "", "", "", false, errors.New("đường dẫn đích được chỉ định hai lần")
		}
		destDir = positionals[1]
	}
	if len(positionals) > 2 {
		return "", "", "", "", false, errors.New("quá nhiều tham số dòng lệnh")
	}
	return repoURL, ref, destDir, workspace, noDeliver, nil
}

func defaultRepoDirName(repoURL string) string {
	trimmed := strings.TrimSuffix(strings.TrimSpace(repoURL), "/")
	trimmed = strings.TrimSuffix(trimmed, ".git")
	if u, err := url.Parse(trimmed); err == nil && u.Path != "" {
		base := filepath.Base(u.Path)
		if base != "" && base != "." && base != "/" {
			return base
		}
	}
	parts := strings.Split(trimmed, "/")
	if last := parts[len(parts)-1]; last != "" {
		return last
	}
	return "repository"
}

func handleClone(args []string, opts *Options) int {
	repoURL, ref, destDir, workspace, noDeliver, err := parseCloneArgs(args)
	if err != nil {
		fmt.Fprintf(opts.Stderr, "Lỗi tham số: %v\n", err)
		return 1
	}

	repository, parseErr := url.Parse(repoURL)
	if parseErr != nil || repository.Scheme != "https" || repository.User != nil || repository.Host == "" {
		fmt.Fprintln(opts.Stderr, "Clone được quản lý yêu cầu URL HTTPS không chứa thông tin xác thực.")
		return 1
	}

	token, err := ResolveToken(opts)
	if err != nil {
		fmt.Fprintf(opts.Stderr, "Lỗi xác thực CLI: %v\n", err)
		return 1
	}

	baseURL := resolveAPIBaseURL(opts)
	client := resolveHTTPClient(opts)
	pollPeriod := opts.PollInterval
	if pollPeriod <= 0 {
		pollPeriod = defaultPollPeriod
	}
	maxWait := opts.MaxWait
	if maxWait <= 0 {
		maxWait = defaultMaxWait
	}

	if !noDeliver {
		if destDir == "" {
			destDir = defaultRepoDirName(repoURL)
		}
		absDest, absErr := filepath.Abs(destDir)
		if absErr != nil {
			fmt.Fprintf(opts.Stderr, "Lỗi xác định đường dẫn đích: %v\n", absErr)
			return 1
		}
		destDir = absDest
	}

	fmt.Fprintln(opts.Stdout, "==========================================================")
	fmt.Fprintln(opts.Stdout, "LabOps Repository Guard — Secure AI Workspace Gateway")
	fmt.Fprintln(opts.Stdout, "==========================================================")
	fmt.Fprintf(opts.Stdout, "Kho mã:    %s\n", repoURL)
	fmt.Fprintf(opts.Stdout, "Nhánh/Ref: %s\n", ref)
	if destDir != "" && !noDeliver {
		fmt.Fprintf(opts.Stdout, "Đích:      %s\n", destDir)
	}

	submitPayload := map[string]string{
		"repo_url": repoURL,
		"ref":      ref,
	}
	if workspace != "" {
		submitPayload["target_workspace"] = workspace
	}

	submitCtx, cancelSubmit := context.WithTimeout(context.Background(), defaultHTTPTimeout)
	code, respBytes, err := doJSONRequest(submitCtx, client, token, http.MethodPost, baseURL+"/requests", submitPayload)
	cancelSubmit()
	if err != nil {
		fmt.Fprintf(opts.Stderr, "Lỗi kết nối tới LabOps API (%s): %v\n", baseURL, err)
		return 1
	}
	if code != http.StatusAccepted && code != http.StatusOK {
		fmt.Fprintf(opts.Stderr, "Yêu cầu bị từ chối (%d): %s\n", code, strings.TrimSpace(string(respBytes)))
		return 1
	}

	var reqData requestStatus
	if err := json.Unmarshal(respBytes, &reqData); err != nil || reqData.ID == "" {
		fmt.Fprintf(opts.Stderr, "Phản hồi không hợp lệ từ server: %s\n", strings.TrimSpace(string(respBytes)))
		return 1
	}

	fmt.Fprintf(opts.Stdout, "Mã yêu cầu: %s\n", reqData.ID)
	fmt.Fprintln(opts.Stdout, "Đang xử lý quy trình kiểm duyệt an toàn...")

	waitCtx, cancelWait := context.WithTimeout(context.Background(), maxWait)
	defer cancelWait()
	lastState := ""

	for {
		if waitCtx.Err() != nil {
			fmt.Fprintf(opts.Stderr, "Hết thời gian chờ kết quả kiểm duyệt cho yêu cầu %s\n", reqData.ID)
			return 1
		}

		pollCtx, cancelPoll := context.WithTimeout(waitCtx, defaultHTTPTimeout)
		statusCode, bodyBytes, pollErr := doJSONRequest(pollCtx, client, token, http.MethodGet, baseURL+"/requests/"+url.PathEscape(reqData.ID), nil)
		cancelPoll()

		if pollErr != nil || statusCode >= 500 {
			waitForPoll(waitCtx, pollPeriod)
			continue
		}
		if statusCode != http.StatusOK {
			fmt.Fprintf(opts.Stderr, "Lỗi phân quyền (%d): %s\n", statusCode, strings.TrimSpace(string(bodyBytes)))
			return 1
		}

		var current requestStatus
		if err := json.Unmarshal(bodyBytes, &current); err != nil || current.ID != reqData.ID || current.State == "" {
			fmt.Fprintln(opts.Stderr, "Phản hồi trạng thái không hợp lệ")
			return 1
		}

		if current.State != lastState {
			lastState = current.State
			fmt.Fprintf(opts.Stdout, "  -> Trạng thái: %s\n", current.State)
		}

		switch current.State {
		case "AUTO_APPROVED", "MANUALLY_APPROVED", "APPROVED", "DELIVERY_FAILED":
			if current.ArtifactDigest != "" {
				fmt.Fprintf(opts.Stdout, "SHA-256 Digest: %s\n", current.ArtifactDigest)
			}
			if noDeliver {
				fmt.Fprintf(opts.Stdout, "Đã phê duyệt (Request ID: %s). Chưa giải nén vì --no-deliver.\n", current.ID)
				return 4
			}

			deliverPayload := map[string]string{
				"request_id":       current.ID,
				"target_directory": destDir,
			}
			if workspace != "" {
				deliverPayload["target_workspace"] = workspace
			}
			delCtx, cancelDel := context.WithTimeout(context.Background(), 150*time.Second)
			delCode, delBody, delErr := doJSONRequest(delCtx, client, token, http.MethodPost, baseURL+"/deliver", deliverPayload)
			cancelDel()
			if delErr != nil || !deliveryConfirmed(delCode, delBody) {
				fmt.Fprintf(opts.Stderr, "Phân phối thất bại (%d): %v %s\n", delCode, delErr, strings.TrimSpace(string(delBody)))
				return 1
			}
			fmt.Fprintf(opts.Stdout, "Hoàn tất phân phối kho mã đã xác minh vào: %s\n", destDir)
			return 0

		case "DELIVERED":
			fmt.Fprintf(opts.Stderr, "Yêu cầu %s đã bàn giao trước đó. Kiểm tra bằng aiotgit status %s.\n", current.ID, current.ID)
			return 1

		case "REVIEW_REQUIRED":
			fmt.Fprintln(opts.Stdout, "REVIEW_REQUIRED: Kho mã đang chờ Admin phê duyệt ngoại lệ.")
			if current.Report != nil {
				for _, r := range current.Report.PolicyReasons {
					fmt.Fprintf(opts.Stdout, "  • %s\n", r)
				}
			}
			fmt.Fprintf(opts.Stdout, "Mã nguồn chưa được phân phối. Kiểm tra bằng aiotgit status %s.\n", current.ID)
			return 3

		case "REJECTED", "REVOKED":
			fmt.Fprintf(opts.Stderr, "%s: Kho mã bị từ chối theo chính sách bảo mật.\n", current.State)
			if current.Report != nil {
				for _, r := range current.Report.PolicyReasons {
					fmt.Fprintf(opts.Stderr, "  • %s\n", r)
				}
			}
			return 2

		case "FAILED", "EXPIRED":
			fmt.Fprintf(opts.Stderr, "%s: %s\n", current.State, current.Error)
			return 1

		case "REQUESTED", "SUBMITTED", "VALIDATING", "RESOLVING_COMMIT", "CHECKING_CACHE",
			"FETCHING", "SCANNING", "POLICY_EVALUATION", "PUBLISHING", "DELIVERING":
			// Continue polling while the broker is processing the request.
		default:
			fmt.Fprintln(opts.Stderr, "Trạng thái yêu cầu không hợp lệ")
			return 1
		}

		waitForPoll(waitCtx, pollPeriod)
	}
}

func waitForPoll(ctx context.Context, period time.Duration) {
	timer := time.NewTimer(period)
	defer timer.Stop()
	select {
	case <-timer.C:
	case <-ctx.Done():
	}
}

func deliveryConfirmed(status int, body []byte) bool {
	var result struct {
		Success bool `json:"success"`
	}
	return status == http.StatusOK && json.Unmarshal(body, &result) == nil && result.Success
}

func handleStatus(args []string, opts *Options) int {
	token, err := ResolveToken(opts)
	if err != nil {
		fmt.Fprintf(opts.Stderr, "Lỗi xác thực CLI: %v\n", err)
		return 1
	}

	baseURL := resolveAPIBaseURL(opts)
	client := resolveHTTPClient(opts)
	ctx, cancel := context.WithTimeout(context.Background(), defaultHTTPTimeout)
	defer cancel()

	if len(args) > 0 && strings.TrimSpace(args[0]) != "" {
		reqID := strings.TrimSpace(args[0])
		code, bodyBytes, err := doJSONRequest(ctx, client, token, http.MethodGet, baseURL+"/requests/"+url.PathEscape(reqID), nil)
		if err != nil {
			fmt.Fprintf(opts.Stderr, "Lỗi kết nối: %v\n", err)
			return 1
		}
		if code >= 400 {
			fmt.Fprintf(opts.Stderr, "Lỗi truy vấn trạng thái (%d): %s\n", code, strings.TrimSpace(string(bodyBytes)))
			return 1
		}
		var pretty bytes.Buffer
		if json.Indent(&pretty, bodyBytes, "", "  ") == nil {
			fmt.Fprintln(opts.Stdout, pretty.String())
		} else {
			fmt.Fprintln(opts.Stdout, string(bodyBytes))
		}
		return 0
	}

	code, bodyBytes, err := doJSONRequest(ctx, client, token, http.MethodGet, baseURL+"/requests", nil)
	if err != nil {
		fmt.Fprintf(opts.Stderr, "Lỗi kết nối: %v\n", err)
		return 1
	}
	if code >= 400 {
		fmt.Fprintf(opts.Stderr, "Lỗi lấy danh sách yêu cầu (%d): %s\n", code, strings.TrimSpace(string(bodyBytes)))
		return 1
	}

	var list []requestStatus
	if err := json.Unmarshal(bodyBytes, &list); err != nil {
		fmt.Fprintf(opts.Stderr, "Lỗi phân tích JSON từ server: %v\n", err)
		return 1
	}

	fmt.Fprintf(opts.Stdout, "%-18s %-34s %-10s %-14s %s\n", "REQUEST ID", "REPOSITORY", "COMMIT", "SUBMITTER", "STATE")
	fmt.Fprintln(opts.Stdout, strings.Repeat("-", 92))
	for _, r := range list {
		shaShort := r.CommitSHA
		if len(shaShort) > 8 {
			shaShort = shaShort[:8]
		}
		repoDisplay := r.RepoURL
		if len(repoDisplay) > 32 {
			repoDisplay = repoDisplay[:29] + "..."
		}
		fmt.Fprintf(opts.Stdout, "%-18s %-34s %-10s %-14s %s\n", r.ID, repoDisplay, shaShort, r.Submitter, r.State)
	}
	return 0
}

func handleDeliver(args []string, opts *Options) int {
	if len(args) != 2 {
		fmt.Fprintln(opts.Stderr, "Cú pháp: gitaiot deliver <request_id> <dest_dir>")
		return 1
	}

	token, err := ResolveToken(opts)
	if err != nil {
		fmt.Fprintf(opts.Stderr, "Lỗi xác thực CLI: %v\n", err)
		return 1
	}

	absDest, err := filepath.Abs(args[1])
	if err != nil {
		fmt.Fprintf(opts.Stderr, "Đường dẫn đích không hợp lệ: %v\n", err)
		return 1
	}

	baseURL := resolveAPIBaseURL(opts)
	client := resolveHTTPClient(opts)
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Second)
	defer cancel()

	payload := map[string]string{
		"request_id":       args[0],
		"target_directory": absDest,
	}
	code, respBytes, err := doJSONRequest(ctx, client, token, http.MethodPost, baseURL+"/deliver", payload)
	if err != nil {
		fmt.Fprintf(opts.Stderr, "Lỗi kết nối: %v\n", err)
		return 1
	}
	if !deliveryConfirmed(code, respBytes) {
		fmt.Fprintf(opts.Stderr, "Phân phối thất bại (%d): %s\n", code, strings.TrimSpace(string(respBytes)))
		return 1
	}
	fmt.Fprintln(opts.Stdout, string(respBytes))
	return 0
}

func handleLegacyRepo(args []string, opts *Options) int {
	if len(args) == 0 {
		fmt.Fprintln(opts.Stderr, "Cần chỉ định lệnh phụ cho 'repo': list, status, deliver")
		return 1
	}
	switch args[0] {
	case "list":
		return handleStatus(nil, opts)
	case "status":
		if len(args) < 2 {
			fmt.Fprintln(opts.Stderr, "Cần cung cấp request_id")
			return 1
		}
		return handleStatus(args[1:], opts)
	case "deliver":
		return handleDeliver(args[1:], opts)
	default:
		fmt.Fprintf(opts.Stderr, "Lệnh phụ không hợp lệ: %s\n", args[0])
		return 1
	}
}
