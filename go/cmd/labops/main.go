package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

func getAgentBaseURL() string {
	u := os.Getenv("LABOPS_AGENT_URL")
	if u == "" {
		return "http://127.0.0.1:9100/api/v1/agent/repositoryguard"
	}
	return strings.TrimSuffix(u, "/") + "/api/v1/agent/repositoryguard"
}

func getAgentToken() string {
	t := os.Getenv("LABOPS_AGENT_SECRET")
	if t == "" {
		return "labops-local-agent-verified-token-2026"
	}
	return t
}

func main() {
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(1)
	}

	command := os.Args[1]
	switch command {
	case "clone":
		handleClone(os.Args[2:])
	case "repo":
		handleRepo(os.Args[2:])
	case "version", "--version", "-v":
		fmt.Println("LabOps CLI v2.0.0 (Repository Security Gateway Client)")
	case "help", "--help", "-h":
		printUsage()
	default:
		fmt.Fprintf(os.Stderr, "Lệnh không hợp lệ: %s\n", command)
		printUsage()
		os.Exit(1)
	}
}

func printUsage() {
	fmt.Println(`LabOps Control Center — Repository Security Gateway CLI

Cú pháp:
  labops clone <repo_url> [--ref <ref>] [--dest <path>]
  labops repo list
  labops repo status <request_id>
  labops repo deliver <request_id> <dest_path>

Ví dụ:
  labops clone https://github.com/torvalds/linux.git
  labops clone https://github.com/gin-gonic/gin --ref v1.9.1
  labops repo status req-a1b2c3d4e5f6`)
}

func handleClone(args []string) {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "Lỗi: Cần cung cấp URL kho lưu trữ GitHub (ví dụ: labops clone https://github.com/owner/repo)")
		os.Exit(1)
	}

	repoURL := args[0]
	ref := "HEAD"
	destDir := ""

	for i := 1; i < len(args); i++ {
		if args[i] == "--ref" && i+1 < len(args) {
			ref = args[i+1]
			i++
		} else if args[i] == "--dest" && i+1 < len(args) {
			destDir = args[i+1]
			i++
		}
	}

	submitter := os.Getenv("USER")
	if submitter == "" {
		submitter = "researcher"
	}

	fmt.Println("==========================================================")
	fmt.Println("🛡️  LabOps Repository Guard — Automated Security Gateway")
	fmt.Println("==========================================================")
	fmt.Printf("Kho mã:   %s\n", repoURL)
	fmt.Printf("Nhánh/Ref: %s\n", ref)
	fmt.Printf("Người gửi: %s\n", submitter)
	fmt.Println("----------------------------------------------------------")

	// 1. Gửi yêu cầu
	payload := map[string]string{
		"repo_url":  repoURL,
		"ref":       ref,
		"submitter": submitter,
	}
	body, _ := json.Marshal(payload)

	req, err := http.NewRequest(http.MethodPost, getAgentBaseURL()+"/requests", bytes.NewReader(body))
	if err != nil {
		fmt.Fprintf(os.Stderr, "Lỗi tạo HTTP request: %v\n", err)
		os.Exit(1)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-LabOps-Agent-Secret", getAgentToken())

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Lỗi kết nối đến LabOps Agent (%s): %v\n", getAgentBaseURL(), err)
		os.Exit(1)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusAccepted && resp.StatusCode != http.StatusOK {
		respBody, _ := io.ReadAll(resp.Body)
		fmt.Fprintf(os.Stderr, "Lỗi từ chối yêu cầu (%d): %s\n", resp.StatusCode, string(respBody))
		os.Exit(1)
	}

	var reqData struct {
		ID        string `json:"id"`
		CommitSHA string `json:"commit_sha"`
		State     string `json:"state"`
		CacheHit  bool   `json:"cache_hit"`
		Error     string `json:"error"`
	}
	_ = json.NewDecoder(resp.Body).Decode(&reqData)

	fmt.Printf("Mã yêu cầu: %s\n", reqData.ID)
	fmt.Println("Đang xử lý quy trình kiểm duyệt an toàn...")

	// 2. Chờ đợi và theo dõi tiến độ
	lastState := ""
	for {
		time.Sleep(1500 * time.Millisecond)

		statusReq, _ := http.NewRequest(http.MethodGet, fmt.Sprintf("%s/requests/%s", getAgentBaseURL(), reqData.ID), nil)
		statusReq.Header.Set("X-LabOps-Agent-Secret", getAgentToken())
		statusResp, err := http.DefaultClient.Do(statusReq)
		if err != nil {
			continue
		}

		var current struct {
			ID             string `json:"id"`
			CommitSHA      string `json:"commit_sha"`
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

		_ = json.NewDecoder(statusResp.Body).Decode(&current)
		statusResp.Body.Close()

		if current.State != lastState {
			lastState = current.State
			switch current.State {
			case "RESOLVING_COMMIT":
				fmt.Println("🔍 Đang xác minh nguồn kho mã và phân giải commit SHA...")
			case "CHECKING_CACHE":
				fmt.Printf("⚡ Đã gắn commit SHA: %s\n", current.CommitSHA)
				fmt.Println("📦 Đang kiểm tra bộ nhớ đệm kho mã đã được phê duyệt...")
			case "FETCHING":
				fmt.Println("📥 Cache Miss -> Đang tải về vùng Quarantine cô lập (No-Exec, Limit 500MB)...")
			case "SCANNING":
				fmt.Println("🔬 Đang chạy Trivy Security Scan (Lỗ hổng CVE, Secrets, Misconfig)...")
			case "POLICY_EVALUATION":
				fmt.Println("⚖️  Đang đánh giá theo chính sách bảo mật LabOps...")
			}
		}

		// Xử lý các trạng thái kết thúc
		if current.State == "AUTO_APPROVED" || current.State == "MANUALLY_APPROVED" {
			fmt.Println("----------------------------------------------------------")
			if current.CacheHit {
				fmt.Println("✅ [CACHE HIT] Kho mã đã được phê duyệt trước đó và còn hiệu lực!")
			} else {
				fmt.Println("✅ [AUTO_APPROVED] Kho mã đạt 100% tiêu chuẩn an toàn LabOps!")
			}
			if current.ArtifactDigest != "" {
				fmt.Printf("🔒 Chữ ký số SHA-256 Digest: %s\n", current.ArtifactDigest)
			}

			// Phân phối mã vào workspace nếu có chỉ định thư mục hoặc mặc định
			if destDir == "" {
				repoParts := strings.Split(strings.TrimSuffix(repoURL, ".git"), "/")
				destDir = repoParts[len(repoParts)-1]
			}

			fmt.Printf("🚀 Đang phân phối mã nguồn đã xác minh vào: %s ...\n", destDir)
			deliverPayload := map[string]string{
				"request_id":       current.ID,
				"target_directory": destDir,
			}
			delBody, _ := json.Marshal(deliverPayload)
			delReq, _ := http.NewRequest(http.MethodPost, getAgentBaseURL()+"/deliver", bytes.NewReader(delBody))
			delReq.Header.Set("Content-Type", "application/json")
			delReq.Header.Set("X-LabOps-Agent-Secret", getAgentToken())
			delResp, errDel := http.DefaultClient.Do(delReq)
			if errDel == nil && (delResp.StatusCode == http.StatusOK || delResp.StatusCode == http.StatusAccepted) {
				fmt.Println("🎉 Hoàn tất phân phối mã! Thư mục đã sẵn sàng để nghiên cứu.")
			} else {
				fmt.Printf("⚠️  Mã nguồn đã được phê duyệt nhưng phân phối tự động gặp lỗi (thử lại qua: labops repo deliver %s <thư_mục>)\n", current.ID)
			}
			return

		} else if current.State == "REVIEW_REQUIRED" {
			fmt.Println("----------------------------------------------------------")
			fmt.Println("⚠️  TRẠNG THÁI: REVIEW_REQUIRED (Chờ quản trị viên xem xét)")
			fmt.Println("Phát hiện các phát hiện bảo mật cần sự phê duyệt ngoại lệ từ Admin:")
			if current.Report != nil {
				for _, r := range current.Report.PolicyReasons {
					fmt.Printf("  • %s\n", r)
				}
			}
			fmt.Printf("\n🔒 Mã nguồn CHƯA ĐƯỢC PHÂN PHỐI vào workspace.\nMã yêu cầu: %s\n", current.ID)
			return

		} else if current.State == "REJECTED" {
			fmt.Println("----------------------------------------------------------")
			fmt.Println("❌ TRẠNG THÁI: REJECTED (Bị từ chối theo chính sách bảo mật)")
			if current.Report != nil {
				for _, r := range current.Report.PolicyReasons {
					fmt.Printf("  • %s\n", r)
				}
			}
			fmt.Println("⛔ Quyền tải về bị chặn nghiêm ngặt (Fail-Closed).")
			os.Exit(2)

		} else if current.State == "FAILED" {
			fmt.Println("----------------------------------------------------------")
			fmt.Printf("❌ TIẾN TRÌNH THẤT BẠI: %s\n", current.Error)
			os.Exit(1)
		}
	}
}

func handleRepo(args []string) {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "Cần chỉ định lệnh phụ cho 'repo': list, status, deliver")
		os.Exit(1)
	}

	subCmd := args[0]
	switch subCmd {
	case "list":
		req, _ := http.NewRequest(http.MethodGet, getAgentBaseURL()+"/requests", nil)
		req.Header.Set("X-LabOps-Agent-Secret", getAgentToken())
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Lỗi kết nối: %v\n", err)
			os.Exit(1)
		}
		defer resp.Body.Close()

		var list []struct {
			ID        string `json:"id"`
			RepoURL   string `json:"repo_url"`
			CommitSHA string `json:"commit_sha"`
			Submitter string `json:"submitted_by"`
			State     string `json:"state"`
		}
		_ = json.NewDecoder(resp.Body).Decode(&list)

		fmt.Printf("%-18s %-32s %-10s %-16s %s\n", "REQUEST ID", "REPOSITORY", "COMMIT", "SUBMITTER", "STATE")
		fmt.Println(strings.Repeat("-", 90))
		for _, r := range list {
			shaShort := r.CommitSHA
			if len(shaShort) > 8 {
				shaShort = shaShort[:8]
			}
			repoDisplay := r.RepoURL
			if len(repoDisplay) > 30 {
				repoDisplay = repoDisplay[:27] + "..."
			}
			fmt.Printf("%-18s %-32s %-10s %-16s %s\n", r.ID, repoDisplay, shaShort, r.Submitter, r.State)
		}

	case "status":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "Cần cung cấp Request ID (ví dụ: labops repo status req-123456)")
			os.Exit(1)
		}
		reqID := args[1]
		req, _ := http.NewRequest(http.MethodGet, fmt.Sprintf("%s/requests/%s", getAgentBaseURL(), reqID), nil)
		req.Header.Set("X-LabOps-Agent-Secret", getAgentToken())
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Lỗi: %v\n", err)
			os.Exit(1)
		}
		defer resp.Body.Close()

		body, _ := io.ReadAll(resp.Body)
		var pretty bytes.Buffer
		_ = json.Indent(&pretty, body, "", "  ")
		fmt.Println(pretty.String())

	case "deliver":
		if len(args) < 3 {
			fmt.Fprintln(os.Stderr, "Cú pháp: labops repo deliver <request_id> <thư_mục_đích>")
			os.Exit(1)
		}
		reqID := args[1]
		destDir := args[2]

		payload := map[string]string{
			"request_id":       reqID,
			"target_directory": destDir,
		}
		body, _ := json.Marshal(payload)
		req, _ := http.NewRequest(http.MethodPost, getAgentBaseURL()+"/deliver", bytes.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-LabOps-Agent-Secret", getAgentToken())

		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Lỗi: %v\n", err)
			os.Exit(1)
		}
		defer resp.Body.Close()

		resBody, _ := io.ReadAll(resp.Body)
		fmt.Println(string(resBody))

	default:
		fmt.Fprintf(os.Stderr, "Lệnh phụ không hợp lệ: %s\n", subCmd)
		os.Exit(1)
	}
}
