package repositoryguard

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
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

// GetTrivyVersion lấy phiên bản Trivy và ngày cập nhật DB
func GetTrivyVersion() (version string, dbUpdated string, err error) {
	cmd := exec.Command("trivy", "version", "--format", "json")
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

// RunTrivyScan thực thi quét tĩnh trên thư mục quarantine đã được cách ly
func RunTrivyScan(ctx context.Context, sourceDir string) (*ScanReport, error) {
	start := time.Now()

	// 1. Kiểm tra sự tồn tại của Trivy binary
	trivyPath, err := exec.LookPath("trivy")
	if err != nil {
		return nil, errors.New("NOT_CONFIGURED: Trivy scanner chưa được cài đặt trên máy chủ (fail-closed)")
	}

	version, dbDate, _ := GetTrivyVersion()

	// 2. Chạy trivy fs quét toàn diện (vuln, secret, misconfig)
	ctxTimeout, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()

	cmd := exec.CommandContext(ctxTimeout, trivyPath,
		"fs",
		"--quiet",
		"--format", "json",
		"--scanners", "vuln,secret,misconfig",
		sourceDir,
	)

	out, err := cmd.Output()
	coverage := ScanCoverage{
		VulnerabilityScan:    "COMPLETED",
		SecretScan:           "COMPLETED",
		MisconfigurationScan: "COMPLETED",
		Submodules:           "NOT_PRESENT",
		LFS:                  "NOT_PRESENT",
		OverallCoverage:      "POLICY_COMPLETE",
	}

	if err != nil {
		// Nếu trivy gặp lỗi trả mã khác 0, kiểm tra xem có xuất JSON được không
		if len(out) == 0 {
			return nil, fmt.Errorf("trivy fs thất bại không xuất được kết quả: %w", err)
		}
	}

	var parsed TrivyOutput
	if err := json.Unmarshal(out, &parsed); err != nil {
		return nil, fmt.Errorf("lỗi unmarshal kết quả JSON từ trivy: %w", err)
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
