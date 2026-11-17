package process

import (
	"os"
	"strings"
	"syscall"
	"testing"
	"time"
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

func TestSanitizeCmdlineRedactsSensitiveFlagsAndAssignments(t *testing.T) {
	input := `python train.py --api-key=flag-secret --client-secret "two word secret" AWS_SECRET_ACCESS_KEY=env-secret Authorization: Bearer header-secret`
	got := sanitizeCmdline(input)
	for _, secret := range []string{"flag-secret", "two word secret", "env-secret", "header-secret"} {
		if strings.Contains(got, secret) {
			t.Errorf("sensitive value %q leaked in sanitized command line %q", secret, got)
		}
	}
	for _, marker := range []string{"--api-key=********", "--client-secret ********", "AWS_SECRET_ACCESS_KEY=********", "Authorization: Bearer ********"} {
		if !strings.Contains(got, marker) {
			t.Errorf("expected redaction marker %q in %q", marker, got)
		}
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

func TestProcessTreeRejectsDifferentNodeReference(t *testing.T) {
	collector := NewCollector("rack", nil)
	_, err := collector.GetProcessTree(BuildRef("workstation", "host", 99999999, 1))
	if err == nil || !strings.Contains(err.Error(), "PROCESS_REF_NODE_MISMATCH") {
		t.Fatalf("expected node mismatch denial, got %v", err)
	}
}

func TestProcessTreeRejectsContainerIdentityMismatch(t *testing.T) {
	collector := NewCollector("rack", nil)
	process, err := collector.GetProcessByPID(os.Getpid())
	if err != nil {
		t.Fatalf("could not read current test process: %v", err)
	}
	_, err = collector.GetProcessTree(BuildRef("rack", "spoofed-container", process.PID, process.StartTimeTicks))
	if err == nil || !strings.Contains(err.Error(), "PROCESS_REF_IDENTITY_MISMATCH") {
		t.Fatalf("expected container identity denial, got %v", err)
	}
}

func TestTerminateRejectsDifferentNodeReference(t *testing.T) {
	controller := NewController(NewCollector("rack", nil))
	_, err := controller.TerminateProcess(BuildRef("workstation", "host", 99999999, 1), 1, false)
	if err == nil || !strings.Contains(err.Error(), "PROCESS_REF_NODE_MISMATCH") {
		t.Fatalf("expected node mismatch denial, got %v", err)
	}
}

func fakeControlProcess(pid int, ref string) *ProcessInfo {
	return &ProcessInfo{
		Ref: ref, PID: pid, PPID: 10, UID: 1001, User: "alice", Name: "python3",
		Classification: ClassResearchJob, ContainerID: "", StartTimeTicks: 456,
	}
}

func fakeController() *Controller {
	controller := NewController(NewCollector("rack", nil))
	controller.readStat = func(int) (uint64, int, string, string, error) {
		return 456, 10, "python3", "R", nil
	}
	controller.sleep = func(time.Duration) {}
	return controller
}

func TestTerminateSignalFailureIsNotReportedAsSuccess(t *testing.T) {
	ref := BuildRef("rack", "host", 12345, 456)
	controller := fakeController()
	controller.lookup = func(pid int) (*ProcessInfo, error) { return fakeControlProcess(pid, ref), nil }
	controller.signal = func(int, syscall.Signal, uint64) error { return syscall.EPERM }
	result, err := controller.TerminateProcess(ref, 1, false)
	if err == nil || !strings.Contains(err.Error(), "SIGNAL_FAILED") {
		t.Fatalf("expected explicit signal failure, got result=%+v err=%v", result, err)
	}
	if result != nil {
		t.Fatalf("failed signal returned a result: %+v", result)
	}
}

func TestKillSignalFailureIsNotReportedAsSuccess(t *testing.T) {
	ref := BuildRef("rack", "host", 12345, 456)
	controller := fakeController()
	controller.lookup = func(pid int) (*ProcessInfo, error) { return fakeControlProcess(pid, ref), nil }
	controller.signal = func(int, syscall.Signal, uint64) error { return syscall.EPERM }
	result, err := controller.KillProcess(ref, true, false)
	if err == nil || !strings.Contains(err.Error(), "SIGNAL_FAILED") {
		t.Fatalf("expected explicit signal failure, got result=%+v err=%v", result, err)
	}
	if result != nil {
		t.Fatalf("failed signal returned a result: %+v", result)
	}
}

func TestTerminateVerificationReadFailureIsNotReportedAsSuccess(t *testing.T) {
	ref := BuildRef("rack", "host", 12345, 456)
	controller := fakeController()
	reads := 0
	controller.readStat = func(int) (uint64, int, string, string, error) {
		reads++
		if reads <= 2 {
			return 456, 10, "python3", "R", nil
		}
		return 0, 0, "", "", syscall.EPERM
	}
	controller.lookup = func(pid int) (*ProcessInfo, error) { return fakeControlProcess(pid, ref), nil }
	controller.signal = func(int, syscall.Signal, uint64) error { return nil }

	result, err := controller.TerminateProcess(ref, 1, false)
	if err == nil || !strings.Contains(err.Error(), "PROCESS_VERIFY_FAILED") {
		t.Fatalf("expected post-signal verification error, got result=%+v err=%v", result, err)
	}
	if result != nil {
		t.Fatalf("verification error returned a success result: %+v", result)
	}
}

func TestKillVerificationReadFailureIsNotReportedAsSuccess(t *testing.T) {
	ref := BuildRef("rack", "host", 12345, 456)
	controller := fakeController()
	reads := 0
	controller.readStat = func(int) (uint64, int, string, string, error) {
		reads++
		if reads <= 2 {
			return 456, 10, "python3", "R", nil
		}
		return 0, 0, "", "", syscall.EPERM
	}
	controller.lookup = func(pid int) (*ProcessInfo, error) { return fakeControlProcess(pid, ref), nil }
	controller.signal = func(int, syscall.Signal, uint64) error { return nil }

	result, err := controller.KillProcess(ref, true, false)
	if err == nil || !strings.Contains(err.Error(), "PROCESS_VERIFY_FAILED") {
		t.Fatalf("expected post-signal verification error, got result=%+v err=%v", result, err)
	}
	if result != nil {
		t.Fatalf("verification error returned a success result: %+v", result)
	}
}

func TestCollectorDoesNotHardcodeUIDToUsernameMappings(t *testing.T) {
	collector := NewCollector("test-node", nil)
	for _, uid := range []int{0, 1000, 1001} {
		if _, ok := collector.uidToUser[uid]; ok {
			t.Fatalf("UID %d has a hardcoded identity mapping", uid)
		}
	}
}

func TestTreeStopRejectsChildOwnedByDifferentUserBeforeSignaling(t *testing.T) {
	ref := BuildRef("rack", "host", 12345, 456)
	controller := fakeController()
	root := fakeControlProcess(12345, ref)
	controller.lookup = func(int) (*ProcessInfo, error) { return root, nil }
	controller.lookupTree = func(string) (*ProcessInfo, error) {
		return &ProcessInfo{PID: root.PID, StartTimeTicks: root.StartTimeTicks, Children: []ProcessInfo{{
			PID: 12346, PPID: root.PID, UID: 1002, Name: "python3", Classification: ClassResearchJob,
		}}}, nil
	}
	signals := 0
	controller.signal = func(int, syscall.Signal, uint64) error { signals++; return nil }
	result, err := controller.TerminateProcess(ref, 1, true)
	if err == nil || !strings.Contains(err.Error(), "PROCESS_TREE_OWNERSHIP_MISMATCH") {
		t.Fatalf("expected child ownership denial, got result=%+v err=%v", result, err)
	}
	if signals != 0 {
		t.Fatalf("sent %d signals before ownership checks completed", signals)
	}
}
