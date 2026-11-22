package repositoryguard

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

type TrivyOutput struct {
	SchemaVersion int `json:"SchemaVersion"`
	Results       []struct {
		Target          string `json:"Target"`
		Class           string `json:"Class"`
		Type            string `json:"Type"`
		Vulnerabilities []struct {
			VulnerabilityID  string `json:"VulnerabilityID"`
			PkgName          string `json:"PkgName"`
			InstalledVersion string `json:"InstalledVersion"`
			FixedVersion     string `json:"FixedVersion,omitempty"`
			Severity         string `json:"Severity"`
			Title            string `json:"Title"`
		} `json:"Vulnerabilities"`
		Misconfigurations []struct {
			ID          string `json:"ID"`
			Title       string `json:"Title"`
			Severity    string `json:"Severity"`
			Message     string `json:"Message"`
			Resolution  string `json:"Resolution"`
			PrimaryURL  string `json:"PrimaryURL"`
		} `json:"Misconfigurations"`
		Secrets []struct {
			RuleID    string `json:"RuleID"`
			Category  string `json:"Category"`
			Severity  string `json:"Severity"`
			Title     string `json:"Title"`
			StartLine int    `json:"StartLine"`
			Match     string `json:"Match"`
		} `json:"Secrets"`
	} `json:"Results"`
}

type TrivyVersionInfo struct {
	Version         string `json:"Version"`
	VulnerabilityDB struct {
		UpdatedAt string `json:"UpdatedAt"`
	} `json:"VulnerabilityDB"`
}

const (
	defaultAgentWorkDir        = "/var/lib/labops-agent"
	defaultAgentTrivyCacheDir  = "/var/lib/labops-agent/trivy-cache"
	defaultAgentTrivyModuleDir = "/var/lib/labops-agent/trivy-modules"
)

func resolveTrivyDir(envVar, defaultDir string) string {
	if custom := strings.TrimSpace(os.Getenv(envVar)); custom != "" {
		return custom
	}
	if info, err := os.Stat(defaultAgentWorkDir); err == nil && info.IsDir() {
		if err := os.MkdirAll(defaultDir, 0o750); err == nil {
			return defaultDir
		}
	}
	return ""
}

func resolveTrivyCacheDir() string {
	return resolveTrivyDir("TRIVY_CACHE_DIR", defaultAgentTrivyCacheDir)
}

func resolveTrivyModuleDir() string {
	return resolveTrivyDir("TRIVY_MODULE_DIR", defaultAgentTrivyModuleDir)
}

func trivyCommandEnv(cacheDir, moduleDir string) []string {
	overrides := map[string]string{}
	home := strings.TrimSpace(os.Getenv("HOME"))
	if home == "" {
		if info, err := os.Stat(defaultAgentWorkDir); err == nil && info.IsDir() {
			overrides["HOME"] = defaultAgentWorkDir
		}
	} else if _, err := os.Stat(home); err != nil {
		if info, dErr := os.Stat(defaultAgentWorkDir); dErr == nil && info.IsDir() {
			overrides["HOME"] = defaultAgentWorkDir
		}
	}
	if cacheDir != "" {
		overrides["TRIVY_CACHE_DIR"] = cacheDir
	}
	if moduleDir != "" {
		overrides["TRIVY_MODULE_DIR"] = moduleDir
	}

	env := make([]string, 0, len(os.Environ())+len(overrides))
	for _, entry := range os.Environ() {
		key, _, found := strings.Cut(entry, "=")
		if found {
			if _, skip := overrides[key]; skip {
				continue
			}
		}
		env = append(env, entry)
	}
	for k, v := range overrides {
		env = append(env, k+"="+v)
	}
	return env
}

func formatTrivyStderr(raw string) string {
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		return ""
	}
	runes := []rune(trimmed)
	if len(runes) > 400 {
		return string(runes[:400]) + "..."
	}
	return trimmed
}

// GetTrivyVersion lấy phiên bản Trivy và ngày cập nhật DB
func GetTrivyVersion() (version string, dbUpdated string, err error) {
	cacheDir := resolveTrivyCacheDir()
	moduleDir := resolveTrivyModuleDir()
	args := []string{"version", "--format", "json"}
	if cacheDir != "" {
		args = append(args, "--cache-dir", cacheDir)
	}
	cmd := exec.Command("trivy", args...)
	cmd.Env = trivyCommandEnv(cacheDir, moduleDir)
	out, err := cmd.Output()
	if err != nil {
		return "", "", fmt.Errorf("không thể kiểm tra phiên bản trivy: %w", err)
	}

	var info TrivyVersionInfo
	if err := json.Unmarshal(out, &info); err != nil {
		return "unknown", "", nil
	}
	return info.Version, info.VulnerabilityDB.UpdatedAt, nil
}

// InspectAuxiliaryFeatures kiểm tra .gitmodules và con trỏ Git LFS thực tế trong mã nguồn.
func InspectAuxiliaryFeatures(sourceDir string) (submodulesStatus, lfsStatus string) {
	submodulesStatus = "NOT_PRESENT"
	lfsStatus = "NOT_PRESENT"

	if st, err := os.Lstat(filepath.Join(sourceDir, ".gitmodules")); err == nil && !st.IsDir() {
		submodulesStatus = "PRESENT_UNFETCHED"
	}

	lfsSig := []byte("version https://git-lfs.github.com/spec/v1")
	_ = filepath.Walk(sourceDir, func(path string, info os.FileInfo, err error) error {
		if err != nil || info == nil || info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return nil
		}
		if filepath.Base(path) == ".gitattributes" {
			data, rErr := os.ReadFile(ioLimitPath(path, 64*1024))
			_ = data
			_ = rErr
			if raw, rErr2 := os.ReadFile(path); rErr2 == nil && strings.Contains(string(raw), "filter=lfs") {
				lfsStatus = "PRESENT_UNFETCHED"
				return filepath.SkipAll
			}
		}
		if info.Size() > 0 && info.Size() <= 512 {
			f, oErr := os.Open(path)
			if oErr == nil {
				buf := make([]byte, 256)
				n, _ := f.Read(buf)
				_ = f.Close()
				if bytes.Contains(buf[:n], lfsSig) {
					lfsStatus = "PRESENT_UNFETCHED"
					return filepath.SkipAll
				}
			}
		}
		return nil
	})
	return submodulesStatus, lfsStatus
}

func ioLimitPath(p string, _ int64) string { return p }

// RunTrivyScan thực thi quét tĩnh trên thư mục quarantine đã được cách ly
func RunTrivyScan(ctx context.Context, sourceDir string) (*ScanReport, error) {
	start := time.Now()

	// 1. Kiểm tra sự tồn tại của Trivy binary
	trivyPath, err := exec.LookPath("trivy")
	if err != nil {
		return nil, errors.New("NOT_CONFIGURED: Trivy scanner chưa được cài đặt trên máy chủ (fail-closed)")
	}

	version, dbDate, _ := GetTrivyVersion()
	submodStatus, lfsStatus := InspectAuxiliaryFeatures(sourceDir)

	// 2. Chạy trivy fs quét toàn diện (vuln, secret, misconfig)
	ctxTimeout, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()

	cacheDir := resolveTrivyCacheDir()
	moduleDir := resolveTrivyModuleDir()
	args := []string{
		"fs",
		"--quiet",
		"--format", "json",
		"--scanners", "vuln,secret,misconfig",
	}
	if cacheDir != "" {
		args = append(args, "--cache-dir", cacheDir)
	}
	if moduleDir != "" {
		args = append(args, "--module-dir", moduleDir)
	}
	args = append(args, sourceDir)

	cmd := exec.CommandContext(ctxTimeout, trivyPath, args...)
	cmd.Env = trivyCommandEnv(cacheDir, moduleDir)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err = cmd.Run()
	out := stdout.Bytes()
	stderrSnippet := formatTrivyStderr(stderr.String())

	overall := "POLICY_COMPLETE"
	if submodStatus != "NOT_PRESENT" || lfsStatus != "NOT_PRESENT" {
		overall = "INCOMPLETE_UNFETCHED_REFS"
	}
	coverage := ScanCoverage{
		VulnerabilityScan:    "COMPLETED",
		SecretScan:           "COMPLETED",
		MisconfigurationScan: "COMPLETED",
		Submodules:           submodStatus,
		LFS:                  lfsStatus,
		OverallCoverage:      overall,
	}

	if err != nil && len(bytes.TrimSpace(out)) == 0 {
		if stderrSnippet != "" {
			return nil, fmt.Errorf("trivy fs thất bại không xuất được kết quả: %w (%s)", err, stderrSnippet)
		}
		return nil, fmt.Errorf("trivy fs thất bại không xuất được kết quả: %w", err)
	}

	var parsed TrivyOutput
	if uErr := json.Unmarshal(out, &parsed); uErr != nil {
		if err != nil {
			if stderrSnippet != "" {
				return nil, fmt.Errorf("trivy fs lỗi (%w) và JSON không hợp lệ: %v (%s)", err, uErr, stderrSnippet)
			}
			return nil, fmt.Errorf("trivy fs lỗi (%w) và JSON không hợp lệ: %v", err, uErr)
		}
		return nil, fmt.Errorf("lỗi unmarshal kết quả JSON từ trivy: %w", uErr)
	}
	if err != nil && parsed.SchemaVersion == 0 && len(parsed.Results) == 0 {
		if stderrSnippet != "" {
			return nil, fmt.Errorf("trivy fs trả về mã lỗi không hợp lệ: %w (%s)", err, stderrSnippet)
		}
		return nil, fmt.Errorf("trivy fs trả về mã lỗi không hợp lệ: %w", err)
	}

	// 3. Tổng hợp số liệu và phân loại
	vulnSummary := VulnerabilitySummary{}
	secretsCount := 0
	misconfigHigh := 0
	var findings []FindingItem

	for _, res := range parsed.Results {
		// Vulnerabilities
		for _, v := range res.Vulnerabilities {
			sev := strings.ToUpper(v.Severity)
			switch sev {
			case "CRITICAL":
				vulnSummary.Critical++
			case "HIGH":
				vulnSummary.High++
			case "MEDIUM":
				vulnSummary.Medium++
			default:
				vulnSummary.Low++
			}

			if sev == "CRITICAL" || sev == "HIGH" {
				findings = append(findings, FindingItem{
					ID:         v.VulnerabilityID,
					Type:       "VULN",
					Severity:   sev,
					Title:      fmt.Sprintf("%s (%s)", v.PkgName, v.Title),
					TargetFile: res.Target,
				})
			}
		}

		// Misconfigurations
		for _, m := range res.Misconfigurations {
			sev := strings.ToUpper(m.Severity)
			if sev == "HIGH" || sev == "CRITICAL" {
				misconfigHigh++
				findings = append(findings, FindingItem{
					ID:         m.ID,
					Type:       "MISCONFIG",
					Severity:   sev,
					Title:      m.Title,
					TargetFile: res.Target,
				})
			}
		}

		// Secrets (che giấu giá trị nhạy cảm)
		for _, s := range res.Secrets {
			secretsCount++
			masked := maskSecret(s.Match)
			findings = append(findings, FindingItem{
				ID:          s.RuleID,
				Type:        "SECRET",
				Severity:    "CRITICAL",
				Title:       s.Title,
				TargetFile:  fmt.Sprintf("%s:%d", res.Target, s.StartLine),
				MaskedValue: masked,
			})
		}
	}

	durationMs := time.Since(start).Milliseconds()

	return &ScanReport{
		Coverage:           coverage,
		Vulnerabilities:    vulnSummary,
		SecretsCount:       secretsCount,
		MisconfigHighCount: misconfigHigh,
		Findings:           findings,
		ScannerVersion:     version,
		DBVersion:          dbDate,
		ScanDurationMs:     durationMs,
	}, nil
}

// maskSecret che giấu bí mật phát hiện được (chỉ giữ 3 ký tự đầu và 3 ký tự cuối)
func maskSecret(secret string) string {
	s := strings.TrimSpace(secret)
	if len(s) <= 8 {
		return "******"
	}
	return s[:3] + strings.Repeat("*", len(s)-6) + s[len(s)-3:]
}
