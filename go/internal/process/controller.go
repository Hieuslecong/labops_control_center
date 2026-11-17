package process

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

type processStatReader func(pid int) (uint64, int, string, string, error)
type processLookup func(pid int) (*ProcessInfo, error)
type processTreeLookup func(ref string) (*ProcessInfo, error)
type processSignal func(pid int, signal syscall.Signal, startTimeTicks uint64) error

// Controller điều khiển tiến trình đã được xác minh bằng ProcessRef.
type Controller struct {
	collector  *Collector
	serverID   string
	readStat   processStatReader
	lookup     processLookup
	lookupTree processTreeLookup
	signal     processSignal
	sleep      func(time.Duration)
}

func NewController(collector *Collector) *Controller {
	ctrl := &Controller{
		collector: collector,
		readStat:  readProcessStartTimeTicks,
		signal:    signalPIDByFD,
		sleep:     time.Sleep,
	}
	if collector != nil {
		ctrl.serverID = collector.serverID
		ctrl.lookup = collector.GetProcessByPID
		ctrl.lookupTree = collector.GetProcessTree
	}
	return ctrl
}

func signalPIDByFD(pid int, signal syscall.Signal, expectedStartTimeTicks uint64) error {
	fd, err := unix.PidfdOpen(pid, 0)
	if err != nil {
		return err
	}
	defer unix.Close(fd)

	startTicks, _, _, _, err := readProcessStartTimeTicks(pid)
	if err != nil {
		return err
	}
	if startTicks != expectedStartTimeTicks {
		return fmt.Errorf("PID_RECYCLED: PID %d changed before signal delivery", pid)
	}
	return unix.PidfdSendSignal(fd, unix.Signal(signal), nil, 0)
}

type processTarget struct {
	pid            int
	startTimeTicks uint64
	name           string
	process        ProcessInfo
}

func (ctrl *Controller) targets(parsed *ProcessRefStruct, ref string, root *ProcessInfo, includeTree bool) ([]processTarget, error) {
	if err := CheckSafeGuard(*root); err != nil {
		return nil, fmt.Errorf("PROTECTED_PROCESS_DENIED: %w", err)
	}
	targets := []processTarget{{pid: root.PID, startTimeTicks: root.StartTimeTicks, name: root.Name, process: *root}}
	if !includeTree {
		return targets, nil
	}
	if ctrl.lookupTree == nil {
		return nil, fmt.Errorf("PROCESS_TREE_UNAVAILABLE: không thể xác minh cây tiến trình")
	}
	tree, err := ctrl.lookupTree(ref)
	if err != nil {
		return nil, fmt.Errorf("PROCESS_TREE_UNAVAILABLE: %w", err)
	}
	var walk func(ProcessInfo) error
	walk = func(parent ProcessInfo) error {
		for _, child := range parent.Children {
			if len(targets) >= 4096 {
				return fmt.Errorf("PROCESS_TREE_LIMIT: cây tiến trình vượt giới hạn an toàn")
			}
			if child.UID != root.UID || child.ContainerID != root.ContainerID {
				return fmt.Errorf("PROCESS_TREE_OWNERSHIP_MISMATCH: tiến trình con không cùng owner/container")
			}
			if err := CheckSafeGuard(child); err != nil {
				return fmt.Errorf("PROTECTED_PROCESS_DENIED: %w", err)
			}
			targets = append(targets, processTarget{pid: child.PID, startTimeTicks: child.StartTimeTicks, name: child.Name, process: child})
			if err := walk(child); err != nil {
				return err
			}
		}
		return nil
	}
	if tree == nil || tree.PID != root.PID || tree.StartTimeTicks != root.StartTimeTicks {
		return nil, fmt.Errorf("PROCESS_REF_IDENTITY_MISMATCH: process tree root changed")
	}
	if err := walk(*tree); err != nil {
		return nil, err
	}
	return targets, nil
}

func (ctrl *Controller) verifyRef(ref string) (*ProcessRefStruct, *ProcessInfo, error) {
	parsed, err := ParseRef(ref)
	if err != nil {
		return nil, nil, fmt.Errorf("INVALID_PROCESS_REF: %w", err)
	}
	if err := validateProcessNode(parsed, ctrl.serverID); err != nil {
		return nil, nil, err
	}
	if parsed.PID <= 1 {
		return nil, nil, fmt.Errorf("PROTECTED_PROCESS_DENIED: từ chối dừng PID %d", parsed.PID)
	}
	if ctrl.readStat == nil || ctrl.lookup == nil {
		return nil, nil, fmt.Errorf("PROCESS_VERIFICATION_UNAVAILABLE: không có bộ kiểm tra tiến trình")
	}
	realTicks, _, _, _, err := ctrl.readStat(parsed.PID)
	if err != nil {
		return nil, nil, fmt.Errorf("PROCESS_NOT_FOUND: PID %d không tồn tại (%w)", parsed.PID, err)
	}
	if realTicks != parsed.StartTimeTicks {
		return nil, nil, fmt.Errorf("PID_RECYCLED: PID %d đã được tái cấp phát", parsed.PID)
	}
	procInfo, err := ctrl.lookup(parsed.PID)
	if err != nil || procInfo == nil {
		return nil, nil, fmt.Errorf("PROCESS_VERIFICATION_FAILED: không thể xác minh PID %d", parsed.PID)
	}
	if err := validateProcessIdentity(parsed, ctrl.serverID, *procInfo); err != nil {
		return nil, nil, err
	}
	if procInfo.Ref != "" && procInfo.Ref != ref {
		return nil, nil, fmt.Errorf("PROCESS_REF_IDENTITY_MISMATCH: ProcessRef canonical không khớp")
	}
	return parsed, procInfo, nil
}

func (ctrl *Controller) sendTargets(targets []processTarget, sig syscall.Signal) error {
	if ctrl.signal == nil {
		return fmt.Errorf("PROCESS_SIGNAL_UNAVAILABLE: signal mechanism is unavailable")
	}
	for _, target := range targets {
		currentTicks, _, _, _, err := ctrl.readStat(target.pid)
		if err != nil {
			if errors.Is(err, os.ErrNotExist) || errors.Is(err, syscall.ESRCH) {
				continue
			}
			return fmt.Errorf("PROCESS_VERIFY_FAILED: PID %d: %w", target.pid, err)
		}
		if currentTicks != target.startTimeTicks {
			return fmt.Errorf("PID_RECYCLED: PID %d changed before signal", target.pid)
		}
		if err := ctrl.signal(target.pid, sig, target.startTimeTicks); err != nil {
			if errors.Is(err, syscall.ESRCH) || errors.Is(err, os.ErrNotExist) {
				continue
			}
			return fmt.Errorf("SIGNAL_FAILED: không thể gửi tín hiệu tới PID %d: %w", target.pid, err)
		}
	}
	return nil
}

func (ctrl *Controller) targetExited(pid int, expectedTicks uint64) (bool, error) {
	ticks, _, _, state, err := ctrl.readStat(pid)
	if errors.Is(err, os.ErrNotExist) || errors.Is(err, syscall.ESRCH) {
		return true, nil
	}
	if err != nil {
		return false, err
	}
	return ticks != expectedTicks || state == "Z", nil
}

// TerminateProcess gửi SIGTERM tới tiến trình đã xác minh. Mọi phần tử cây
// đều phải cùng UID/container và được gắn pidfd trước khi nhận tín hiệu.
func (ctrl *Controller) TerminateProcess(ref string, timeoutSec int, terminateTree bool) (*ControlResult, error) {
	if timeoutSec <= 0 {
		timeoutSec = 10
	}
	if timeoutSec > 300 {
		return nil, fmt.Errorf("INVALID_TIMEOUT: timeout tối đa là 300 giây")
	}
	parsed, root, err := ctrl.verifyRef(ref)
	if err != nil {
		return nil, err
	}
	targets, err := ctrl.targets(parsed, ref, root, terminateTree)
	if err != nil {
		return nil, err
	}
	if err := ctrl.sendTargets(targets, syscall.SIGTERM); err != nil {
		return nil, err
	}
	deadline := time.Now().Add(time.Duration(timeoutSec) * time.Second)
	for time.Now().Before(deadline) {
		exited, verifyErr := ctrl.targetExited(parsed.PID, parsed.StartTimeTicks)
		if verifyErr != nil {
			return nil, fmt.Errorf("PROCESS_VERIFY_FAILED: cannot verify PID %d after SIGTERM: %w", parsed.PID, verifyErr)
		}
		if exited {
			return &ControlResult{
				Status: "TERMINATED", Action: "SIGTERM", Ref: ref, PID: parsed.PID,
				Message:    fmt.Sprintf("Tiến trình PID %d đã dừng và được hậu kiểm.", parsed.PID),
				ExecutedAt: time.Now().UTC(), PIDsKilled: targetPIDs(targets),
			}, nil
		}
		ctrl.sleep(200 * time.Millisecond)
	}
	return &ControlResult{
		Status: "TIMEOUT_STILL_RUNNING", Action: "SIGTERM", Ref: ref, PID: parsed.PID,
		Message:    fmt.Sprintf("Tiến trình PID %d vẫn còn sau %d giây.", parsed.PID, timeoutSec),
		ExecutedAt: time.Now().UTC(), PIDsKilled: targetPIDs(targets),
	}, nil
}

// KillProcess chỉ trả về KILLED sau khi xác minh PID/start-time đã kết thúc.
func (ctrl *Controller) KillProcess(ref string, force bool, killTree bool) (*ControlResult, error) {
	if !force {
		return nil, fmt.Errorf("FORCE_CONFIRMATION_REQUIRED: SIGKILL cần xác nhận rõ ràng")
	}
	parsed, root, err := ctrl.verifyRef(ref)
	if err != nil {
		return nil, err
	}
	targets, err := ctrl.targets(parsed, ref, root, killTree)
	if err != nil {
		return nil, err
	}
	if err := ctrl.sendTargets(targets, syscall.SIGKILL); err != nil {
		return nil, err
	}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		exited, verifyErr := ctrl.targetExited(parsed.PID, parsed.StartTimeTicks)
		if verifyErr != nil {
			return nil, fmt.Errorf("PROCESS_VERIFY_FAILED: cannot verify PID %d after SIGKILL: %w", parsed.PID, verifyErr)
		}
		if exited {
			return &ControlResult{
				Status: "KILLED", Action: "SIGKILL", Ref: ref, PID: parsed.PID,
				Message:    fmt.Sprintf("Tiến trình PID %d đã kết thúc và được hậu kiểm.", parsed.PID),
				ExecutedAt: time.Now().UTC(), PIDsKilled: targetPIDs(targets),
			}, nil
		}
		ctrl.sleep(100 * time.Millisecond)
	}
	return nil, fmt.Errorf("KILL_NOT_VERIFIED: PID %d vẫn tồn tại sau SIGKILL", parsed.PID)
}

func targetPIDs(targets []processTarget) []int {
	pids := make([]int, 0, len(targets))
	for _, target := range targets {
		pids = append(pids, target.pid)
	}
	return pids
}

// readProcessStartTimeTicks reads a process identity snapshot from /proc.
func readProcessStartTimeTicks(pid int) (uint64, int, string, string, error) {
	statPath := filepath.Join("/proc", strconv.Itoa(pid), "stat")
	data, err := os.ReadFile(statPath)
	if err != nil {
		return 0, 0, "", "", err
	}
	statStr := string(data)
	firstParen := strings.IndexByte(statStr, '(')
	lastParen := strings.LastIndexByte(statStr, ')')
	if firstParen == -1 || lastParen == -1 || lastParen <= firstParen {
		return 0, 0, "", "", fmt.Errorf("cannot parse /proc/%d/stat", pid)
	}
	name := statStr[firstParen+1 : lastParen]
	afterParen := strings.Fields(statStr[lastParen+2:])
	if len(afterParen) < 20 {
		return 0, 0, "", "", fmt.Errorf("/proc/%d/stat is incomplete", pid)
	}
	state := afterParen[0]
	pgid, _ := strconv.Atoi(afterParen[2])
	startTimeTicks, err := strconv.ParseUint(afterParen[19], 10, 64)
	if err != nil {
		return 0, 0, "", "", fmt.Errorf("read process start time: %w", err)
	}
	return startTimeTicks, pgid, name, state, nil
}
