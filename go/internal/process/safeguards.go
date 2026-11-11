package process

import (
	"fmt"
	"strings"
)

// ProtectedProcessNames danh sách tên tiến trình hệ thống bất khả xâm phạm
var ProtectedProcessNames = map[string]bool{
	"systemd":       true,
	"init":          true,
	"kthreadd":      true,
	"dockerd":       true,
	"containerd":    true,
	"docker-proxy":  true,
	"labops-agent":  true,
	"postgres":      true,
	"postgresql":    true,
	"journald":      true,
	"systemd-logind": true,
	"systemd-udevd": true,
}

// ProtectedKeywords từ khóa trong cmdline chỉ các dịch vụ cốt lõi
var ProtectedCmdKeywords = []string{
	"labops-agent",
	"app.main:app",
	"uvicorn app.main",
	"backend/app/main.py",
	"backend/app/main",
	"app/main.py",
	"next-server",
	"next dev",
	"next start",
	"wg-easy",
	"/usr/sbin/dockerd",
	"/usr/bin/containerd",
}

// CheckSafeGuard kiểm tra xem tiến trình có thuộc danh sách bảo vệ tuyệt đối hay không
func CheckSafeGuard(p ProcessInfo) error {
	// 1. Kiểm tra PID và PPID
	if p.PID <= 1 {
		return fmt.Errorf("từ chối dừng: PID %d là tiến trình khởi tạo gốc (init/systemd)", p.PID)
	}
	if p.PPID == 2 || p.PID == 2 {
		return fmt.Errorf("từ chối dừng: PID %d là kernel thread của hệ điều hành", p.PID)
	}

	// 2. Kiểm tra tên tiến trình
	baseName := strings.ToLower(p.Name)
	if ProtectedProcessNames[baseName] {
		return fmt.Errorf("từ chối dừng: tiến trình '%s' (PID %d) thuộc danh sách dịch vụ hệ thống bảo vệ", p.Name, p.PID)
	}

	// 3. Kiểm tra Host SSH daemon (sshd lắng nghe port 1911 hoặc host ssh listener)
	if (strings.Contains(baseName, "sshd") || strings.Contains(p.CommandLine, "sshd")) && p.ContainerID == "" {
		// Tiến trình sshd chạy trực tiếp trên host
		if strings.Contains(p.CommandLine, "-D") || strings.Contains(p.CommandLine, "1911") || p.PPID <= 1 {
			return fmt.Errorf("từ chối dừng: PID %d là Host SSH daemon quản trị máy chủ", p.PID)
		}
	}

	// 4. Kiểm tra từ khóa cmdline cho control plane và web server
	cmdLower := strings.ToLower(p.CommandLine)
	for _, kw := range ProtectedCmdKeywords {
		if strings.Contains(cmdLower, kw) {
			return fmt.Errorf("từ chối dừng: tiến trình chứa '%s' là dịch vụ nền tảng LabOps Control Center", kw)
		}
	}

	// 5. Kiểm tra phân loại
	if p.Classification == ClassSystemProcess && p.ContainerID == "" {
		return fmt.Errorf("từ chối dừng: tiến trình (PID %d) được phân loại là SYSTEM_PROCESS trên host vật lý", p.PID)
	}

	return nil
}
