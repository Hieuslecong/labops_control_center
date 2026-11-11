package process

import (
	"strings"
	"testing"
)

func TestRef_BuildAndParse(t *testing.T) {
	ref := BuildRef("rack", "ws-testacc", 12345, 987654)
	if ref != "rack:ws-testacc:12345:987654" {
		t.Fatalf("BuildRef trả về không đúng: %s", ref)
	}

	parsed, err := ParseRef(ref)
	if err != nil {
		t.Fatalf("ParseRef lỗi: %v", err)
	}
	if parsed.ServerID != "rack" || parsed.ContainerID != "ws-testacc" || parsed.PID != 12345 || parsed.StartTimeTicks != 987654 {
		t.Fatalf("ParseRef không khớp: %+v", parsed)
	}

	// Host process
	hostRef := BuildRef("rack", "", 5555, 1111)
	if hostRef != "rack:host:5555:1111" {
		t.Fatalf("BuildRef host không đúng: %s", hostRef)
	}
}

func TestCheckSafeGuard_ProtectedProcesses(t *testing.T) {
	// 1. PID 1
	p1 := ProcessInfo{PID: 1, PPID: 0, Name: "systemd", Classification: ClassSystemProcess}
	if err := CheckSafeGuard(p1); err == nil {
		t.Errorf("Kỳ vọng CheckSafeGuard từ chối PID 1")
	}

	// 2. Kernel thread PPID 2
	p2 := ProcessInfo{PID: 50, PPID: 2, Name: "kworker/0:0", Classification: ClassSystemProcess}
	if err := CheckSafeGuard(p2); err == nil {
		t.Errorf("Kỳ vọng CheckSafeGuard từ chối Kernel thread")
	}

	// 3. dockerd
	pDocker := ProcessInfo{PID: 1000, PPID: 1, Name: "dockerd", CommandLine: "/usr/sbin/dockerd", Classification: ClassSystemProcess}
	if err := CheckSafeGuard(pDocker); err == nil {
		t.Errorf("Kỳ vọng CheckSafeGuard từ chối dockerd")
	}

	// 4. LabOps Go Agent
	pAgent := ProcessInfo{PID: 2000, PPID: 1, Name: "labops-agent", CommandLine: "./bin/labops-agent", Classification: ClassSystemProcess}
	if err := CheckSafeGuard(pAgent); err == nil {
		t.Errorf("Kỳ vọng CheckSafeGuard từ chối labops-agent")
	}

	// 5. Host SSH Daemon
	pSSH := ProcessInfo{PID: 4249, PPID: 1, Name: "sshd", CommandLine: "/usr/sbin/sshd -D", Classification: ClassSystemProcess}
	if err := CheckSafeGuard(pSSH); err == nil {
		t.Errorf("Kỳ vọng CheckSafeGuard từ chối Host SSH Daemon")
	}

	// 6. Member Process inside container -> CHO PHÉP
	pMember := ProcessInfo{
		PID:            3000,
		PPID:           2999,
		Name:           "python3",
		CommandLine:    "python3 train.py",
		Classification: ClassResearchJob,
		ContainerID:    "ws-testacc",
		Workspace:      "ws-testacc",
	}
	if err := CheckSafeGuard(pMember); err != nil {
		t.Errorf("Kỳ vọng CheckSafeGuard cho phép dừng tiến trình thành viên trong container, lỗi: %v", err)
	}
}

func TestCheckSafeGuard_BackendProtection(t *testing.T) {
	pBackend := ProcessInfo{
		PID:            8888,
		PPID:           1,
		Name:           "python",
		CommandLine:    "python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000",
		Classification: ClassSystemProcess,
	}
	err := CheckSafeGuard(pBackend)
	if err == nil {
		t.Errorf("Kỳ vọng từ chối uvicorn backend")
	} else if !strings.Contains(err.Error(), "dịch vụ nền tảng") {
		t.Errorf("Thông báo lỗi không đúng kỳ vọng: %v", err)
	}
}
