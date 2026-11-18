package process

import (
	"fmt"
	"strings"
	"sync"
	"syscall"
	"testing"
)

type fakeProcBackend struct {
	mu           sync.Mutex
	procs        map[int]*LiveProcInfo
	children     map[int][]*LiveProcInfo
	signalErr    map[int]error
	stayAlive    map[int]bool
	becameZombie map[int]bool
	signalCalls  []int
}

func newFakeProcBackend() *fakeProcBackend {
	return &fakeProcBackend{
		procs:        make(map[int]*LiveProcInfo),
		children:     make(map[int][]*LiveProcInfo),
		signalErr:    make(map[int]error),
		stayAlive:    make(map[int]bool),
		becameZombie: make(map[int]bool),
	}
}

func (f *fakeProcBackend) InspectPID(pid int) (*LiveProcInfo, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	p, ok := f.procs[pid]
	if !ok {
		return nil, fmt.Errorf("no such process %d", pid)
	}
	cp := *p
	return &cp, nil
}

func (f *fakeProcBackend) ListChildrenPIDs(rootPID int) ([]*LiveProcInfo, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	list := f.children[rootPID]
	out := make([]*LiveProcInfo, 0, len(list))
	for _, c := range list {
		cp := *c
		out = append(out, &cp)
	}
	return out, nil
}

func (f *fakeProcBackend) SendSignalVerified(pid int, expectedTicks uint64, expectedUID int, sig syscall.Signal) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.signalCalls = append(f.signalCalls, pid)
	if err, ok := f.signalErr[pid]; ok && err != nil {
		return err
	}
	p, ok := f.procs[pid]
	if !ok {
		return syscall.ESRCH
	}
	if p.StartTimeTicks != expectedTicks {
		return fmt.Errorf("PID_RECYCLED: expected %d got %d", expectedTicks, p.StartTimeTicks)
	}
	if p.UID != expectedUID {
		return fmt.Errorf("PERMISSION_DENIED: UID mismatch")
	}
	if f.stayAlive[pid] {
		return nil
	}
	if f.becameZombie[pid] {
		p.State = "Z"
		return nil
	}
	delete(f.procs, pid)
	return nil
}

func TestProcessController_All9SafetyScenarios(t *testing.T) {
	col := NewCollector("workstation", nil)

	// 1. Permission Denied (EPERM) on root PID -> explicit error (not false KILLED)
	b1 := newFakeProcBackend()
	b1.procs[2001] = &LiveProcInfo{PID: 2001, PPID: 1000, UID: 1011, User: "vinh_test", Name: "python3", CommandLine: "python3 train.py", State: "R", StartTimeTicks: 555}
	b1.signalErr[2001] = fmt.Errorf("PERMISSION_DENIED: %w", syscall.EPERM)
	ctrl1 := NewControllerWithBackend(col, "workstation", b1)
	if _, err := ctrl1.KillProcess("workstation:host:2001:555", true, false); err == nil || !strings.Contains(err.Error(), "PERMISSION_DENIED") {
		t.Fatalf("1: Expected PERMISSION_DENIED error, got: %v", err)
	}

	// 2. PID Reused (StartTimeTicks mismatch)
	b2 := newFakeProcBackend()
	b2.procs[2002] = &LiveProcInfo{PID: 2002, PPID: 1000, UID: 1011, User: "vinh_test", Name: "python3", CommandLine: "python3 new.py", State: "R", StartTimeTicks: 999}
	ctrl2 := NewControllerWithBackend(col, "workstation", b2)
	if _, err := ctrl2.TerminateProcess("workstation:host:2002:111", 1, false); err == nil || !strings.Contains(err.Error(), "PID_RECYCLED") {
		t.Fatalf("2: Expected PID_RECYCLED rejection, got: %v", err)
	}

	// 3. Missing PID -> PROCESS_NOT_FOUND
	b3 := newFakeProcBackend()
	ctrl3 := NewControllerWithBackend(col, "workstation", b3)
	if _, err := ctrl3.TerminateProcess("workstation:host:999999:111", 1, false); err == nil || !strings.Contains(err.Error(), "PROCESS_NOT_FOUND") {
		t.Fatalf("3: Expected PROCESS_NOT_FOUND, got: %v", err)
	}

	// 4. Protected process (even though Collector.latest cache is empty!) + wrong container spoof rejected
	b4 := newFakeProcBackend()
	b4.procs[2004] = &LiveProcInfo{PID: 2004, PPID: 1, UID: 1000, User: "aiot-ws1", Name: "python3", CommandLine: "uvicorn app.main:app --port 8000", State: "S", StartTimeTicks: 777, ContainerID: ""}
	ctrl4 := NewControllerWithBackend(col, "workstation", b4)
	if _, err := ctrl4.KillProcess("workstation:host:2004:777", true, false); err == nil || !strings.Contains(err.Error(), "PROTECTED_PROCESS_DENIED") {
		t.Fatalf("4a: Protected uvicorn service MUST be blocked even on cache miss, got: %v", err)
	}
	if _, err := ctrl4.KillProcess("workstation:fake-container:2004:777", true, false); err == nil || !strings.Contains(err.Error(), "CONTAINER_MISMATCH") {
		t.Fatalf("4b: Container spoofing in ref MUST be blocked, got: %v", err)
	}
	if _, err := ctrl4.KillProcess("other-node:host:2004:777", true, false); err == nil || !strings.Contains(err.Error(), "HOST_MISMATCH") {
		t.Fatalf("4c: Wrong host in ref MUST be blocked, got: %v", err)
	}

	// 5. Descendant owned by DIFFERENT user MUST be skipped & NEVER signaled (-pgid removed)
	b5 := newFakeProcBackend()
	b5.procs[3000] = &LiveProcInfo{PID: 3000, PPID: 1, UID: 1011, User: "vinh_test", Name: "python3", CommandLine: "python3 train.py", State: "R", StartTimeTicks: 800}
	otherUserChild := &LiveProcInfo{PID: 3001, PPID: 3000, UID: 1009, User: "nhannv", Name: "python3", CommandLine: "python3 other.py", State: "R", StartTimeTicks: 801}
	sameUserChild := &LiveProcInfo{PID: 3002, PPID: 3000, UID: 1011, User: "vinh_test", Name: "pt_data_worker", CommandLine: "python3 worker", State: "R", StartTimeTicks: 802}
	b5.procs[3001] = otherUserChild
	b5.procs[3002] = sameUserChild
	b5.children[3000] = []*LiveProcInfo{otherUserChild, sameUserChild}
	ctrl5 := NewControllerWithBackend(col, "workstation", b5)
	res5, err5 := ctrl5.TerminateProcess("workstation:host:3000:800", 1, true)
	if err5 != nil {
		t.Fatalf("5: Unexpected error: %v", err5)
	}
	if res5.Status != "PARTIAL_FAILURE" || len(res5.SkippedDescendants) != 1 {
		t.Fatalf("5: Expected PARTIAL_FAILURE with 1 skipped different-owner child, got: %+v", res5)
	}
	if _, victimStillPresent := b5.procs[3001]; !victimStillPresent {
		t.Fatal("5: Child belonging to different owner MUST NOT be signaled/killed!")
	}

	// 6. Timeout / unkillable D-state process after SIGKILL -> NEVER falsely report KILLED
	b6 := newFakeProcBackend()
	b6.procs[4000] = &LiveProcInfo{PID: 4000, PPID: 1, UID: 1011, User: "vinh_test", Name: "python3", CommandLine: "python3 stuck_io.py", State: "D", StartTimeTicks: 900}
	b6.stayAlive[4000] = true
	ctrl6 := NewControllerWithBackend(col, "workstation", b6)
	res6, err6 := ctrl6.KillProcess("workstation:host:4000:900", true, false)
	if err6 != nil {
		t.Fatalf("6: Unexpected error: %v", err6)
	}
	if res6.Status != "FAILED_STILL_RUNNING" || res6.Verification != "STILL_RUNNING" || len(res6.PIDsRemaining) != 1 {
		t.Fatalf("6: Living D-state process MUST NOT report KILLED; got: %+v", res6)
	}

	// 7. Zombie process ('Z') is treated as exited/verified terminated
	b7 := newFakeProcBackend()
	b7.procs[5000] = &LiveProcInfo{PID: 5000, PPID: 1, UID: 1011, User: "vinh_test", Name: "python3", CommandLine: "python3 z.py", State: "R", StartTimeTicks: 1000}
	b7.becameZombie[5000] = true
	ctrl7 := NewControllerWithBackend(col, "workstation", b7)
	res7, err7 := ctrl7.KillProcess("workstation:host:5000:1000", true, false)
	if err7 != nil || res7.Status != "KILLED" || res7.Verification != "VERIFIED" {
		t.Fatalf("7: Zombie process should be verified exited, got err=%v res=%+v", err7, res7)
	}

	// 8. Partial tree termination (root dies, 1 child stays alive -> PARTIAL_FAILURE)
	b8 := newFakeProcBackend()
	b8.procs[6000] = &LiveProcInfo{PID: 6000, PPID: 1, UID: 1011, User: "vinh_test", Name: "python3", CommandLine: "python3 p.py", State: "R", StartTimeTicks: 1100}
	stuckChild := &LiveProcInfo{PID: 6001, PPID: 6000, UID: 1011, User: "vinh_test", Name: "python3", CommandLine: "python3 c.py", State: "D", StartTimeTicks: 1101}
	b8.procs[6001] = stuckChild
	b8.stayAlive[6001] = true
	b8.children[6000] = []*LiveProcInfo{stuckChild}
	ctrl8 := NewControllerWithBackend(col, "workstation", b8)
	res8, err8 := ctrl8.KillProcess("workstation:host:6000:1100", true, true)
	if err8 != nil || res8.Status != "PARTIAL_FAILURE" || len(res8.PIDsRemaining) != 1 || res8.PIDsRemaining[0] != 6001 {
		t.Fatalf("8: Expected PARTIAL_FAILURE with remaining child 6001, got err=%v res=%+v", err8, res8)
	}

	// 9. Concurrent stop serialization + non-admin authorization check
	b9 := newFakeProcBackend()
	b9.procs[7000] = &LiveProcInfo{PID: 7000, PPID: 1, UID: 1011, User: "vinh_test", Name: "python3", CommandLine: "python3 t.py", State: "R", StartTimeTicks: 1200}
	ctrl9 := NewControllerWithBackend(col, "workstation", b9)
	if _, err := ctrl9.TerminateProcessAuthorized("workstation:host:7000:1200", "nhannv", false, 1, false); err == nil {
		t.Fatal("9a: Non-admin user MUST NOT terminate another user's process")
	}
	var wg sync.WaitGroup
	errs := make([]error, 2)
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			_, errs[idx] = ctrl9.TerminateProcessAuthorized("workstation:host:7000:1200", "vinh_test", false, 1, false)
		}(i)
	}
	wg.Wait()
	// Exact serialization guarantees 1 succeeds and 1 gets PROCESS_NOT_FOUND
	successCnt, notFoundCnt := 0, 0
	for _, e := range errs {
		if e == nil {
			successCnt++
		} else if strings.Contains(e.Error(), "PROCESS_NOT_FOUND") {
			notFoundCnt++
		}
	}
	if successCnt != 1 || notFoundCnt != 1 {
		t.Fatalf("9b: Concurrent stops on same ref should serialize (1 success, 1 not-found), got errs=%v", errs)
	}
}
