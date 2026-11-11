package repositoryguard

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"time"
)

type ScorecardCLIOutput struct {
	Date   string  `json:"date"`
	Repo   struct {
		Name   string `json:"name"`
		Commit string `json:"commit"`
	} `json:"repo"`
	Score  float64 `json:"score"`
	Checks []struct {
		Name   string `json:"name"`
		Score  int    `json:"score"`
		Reason string `json:"reason"`
	} `json:"checks"`
}

// EvaluateScorecard kiểm tra điểm số chuỗi cung ứng OpenSSF Scorecard nếu khả dụng
func EvaluateScorecard(ctx context.Context, repoURL, commitSHA string) ScorecardSummary {
	scorecardPath, err := exec.LookPath("scorecard")
	if err != nil {
		return ScorecardSummary{
			Score:   -1,
			Status:  "NOT_CONFIGURED",
			Reasons: []string{"OpenSSF Scorecard CLI chưa được cài đặt trên máy chủ; sử dụng trạng thái UNKNOWN an toàn."},
		}
	}

	ctxTimeout, cancel := context.WithTimeout(ctx, 45*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctxTimeout, scorecardPath,
		fmt.Sprintf("--repo=%s", repoURL),
		fmt.Sprintf("--commit=%s", commitSHA),
		"--format=json",
	)

	// Đảm bảo không truyền secrets nhạy cảm ngoài token nếu có
	if ghToken := os.Getenv("GITHUB_TOKEN"); ghToken != "" {
		cmd.Env = append(os.Environ(), fmt.Sprintf("GITHUB_AUTH_TOKEN=%s", ghToken))
	}

	out, err := cmd.Output()
	if err != nil {
		return ScorecardSummary{
			Score:   -1,
			Status:  "UNKNOWN",
			Reasons: []string{fmt.Sprintf("Không thể truy vấn Scorecard hoặc vượt ngưỡng rate-limit GitHub API: %v", err)},
		}
	}

	var parsed ScorecardCLIOutput
	if err := json.Unmarshal(out, &parsed); err != nil {
		return ScorecardSummary{
			Score:   -1,
			Status:  "UNKNOWN",
			Reasons: []string{"Lỗi phân tích cú pháp kết quả từ OpenSSF Scorecard JSON"},
		}
	}

	now := time.Now()
	checksMap := make(map[string]int)
	var reasons []string

	for _, c := range parsed.Checks {
		checksMap[c.Name] = c.Score
		if c.Score < 5 && c.Score >= 0 {
			reasons = append(reasons, fmt.Sprintf("%s: điểm thấp (%d/10)", c.Name, c.Score))
		}
	}

	return ScorecardSummary{
		Score:       parsed.Score,
		Status:      "EVALUATED",
		Checks:      checksMap,
		Reasons:     reasons,
		CollectedAt: &now,
	}
}
