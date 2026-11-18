package process

import (
	"bufio"
	"errors"
	"fmt"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

// LiveProcInfo là snapshot đọc trực tiếp (không qua cache 1.5s) từ /proc/<pid>/.
type LiveProcInfo struct {
	PID            int
	PPID           int
	UID            int
	User           string
	Name           string
	CommandLine    string
	State          string
	StartTimeTicks uint64
	ContainerID    string
}

func (l *LiveProcInfo) toProcessInfo() ProcessInfo {
	classification := ClassMemberProcess
	cmdLower := strings.ToLower(l.CommandLine)
	nameLower := strings.ToLower(l.Name)
	isHostSvc := false
	for _, kw := range ProtectedCmdKeywords {
		if strings.Contains(cmdLower, kw) {
			isHostSvc = true
			break
		}
	}
	if l.PID <= 1 || l.PPID == 2 || l.PID == 2 || ProtectedProcessNames[nameLower] || isHostSvc || l.UID == 0 || l.UID < 1000 {
		classification = ClassSystemProcess
	}
	return ProcessInfo{
		PID:            l.PID,
		PPID:           l.PPID,
		UID:            l.UID,
		User:           l.User,
		Name:           l.Name,
		CommandLine:    l.CommandLine,
		Classification: classification,
		ContainerID:    l.ContainerID,
		StartTimeTicks: l.StartTimeTicks,
		State:          l.State,
	}
}

// ProcessBackend trừu tượng hóa thao tác kiểm tra & gửi tín hiệu OS qua pidfd (cho phép fake test an toàn).
type ProcessBackend interface {
	InspectPID(pid int) (*LiveProcInfo, error)
	ListChildrenPIDs(rootPID int) ([]*LiveProcInfo, error)
	SendSignalVerified(pid int, expectedTicks uint64, expectedUID int, sig syscall.Signal) error
}

type osProcessBackend struct{}

func readLiveProc(pid int) (*LiveProcInfo, error) {
	pidPath := filepath.Join("/proc", strconv.Itoa(pid))
	data, err := os.ReadFile(filepath.Join(pidPath, "stat"))
	if err != nil {
		return nil, err
	}
	statStr := string(data)
	firstParen := strings.IndexByte(statStr, '(')
	lastParen := strings.LastIndexByte(statStr, ')')
	if firstParen == -1 || lastParen == -1 || lastParen <= firstParen {
		return nil, fmt.Errorf("không thể phân tích định dạng /proc/%d/stat", pid)
	}
	name := statStr[firstParen+1 : lastParen]
	afterParen := strings.Fields(statStr[lastParen+2:])
	if len(afterParen) < 20 {
		return nil, fmt.Errorf("/proc/%d/stat không đủ trường dữ liệu", pid)
	}
	state := afterParen[0]
	ppid, _ := strconv.Atoi(afterParen[1])
	startTimeTicks, err := strconv.ParseUint(afterParen[19], 10, 64)
	if err != nil {
		return nil, fmt.Errorf("lỗi đọc starttime ticks: %w", err)
	}

	uid := 0
	if statusData, err := os.ReadFile(filepath.Join(pidPath, "status")); err == nil {
		scanner := bufio.NewScanner(strings.NewReader(string(statusData)))
		for scanner.Scan() {
			line := scanner.Text()
			if strings.HasPrefix(line, "Uid:") {
				fields := strings.Fields(line)
				if len(fields) >= 2 {
					uid, _ = strconv.Atoi(fields[1])
				}
				break
			}
		}
	}

	userName := strconv.Itoa(uid)
	if uid == 0 {
		userName = "root"
	} else if u, uErr := user.LookupId(strconv.Itoa(uid)); uErr == nil {
		userName = u.Username
	}

	cmdline := name
	if cmdData, err := os.ReadFile(filepath.Join(pidPath, "cmdline")); err == nil && len(cmdData) > 0 {
		cmdStr := strings.TrimSpace(strings.ReplaceAll(string(cmdData), "\x00", " "))
		if cmdStr != "" {
			cmdline = sanitizeCmdline(cmdStr)
		}
	}

	cid, _ := extractContainerInfo(pid)
	return &LiveProcInfo{
		PID:            pid,
		PPID:           ppid,
		UID:            uid,
		User:           userName,
		Name:           name,
		CommandLine:    cmdline,
		State:          state,
		StartTimeTicks: startTimeTicks,
		ContainerID:    cid,
	}, nil
}

func (osProcessBackend) InspectPID(pid int) (*LiveProcInfo, error) {
	return readLiveProc(pid)
}

func (osProcessBackend) ListChildrenPIDs(rootPID int) ([]*LiveProcInfo, error) {
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return nil, err
	}
	byPPID := make(map[int][]*LiveProcInfo)
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		pid, pErr := strconv.Atoi(e.Name())
		if pErr != nil || pid <= 0 {
			continue
		}
		info, rErr := readLiveProc(pid)
		if rErr != nil {
			continue
		}
		byPPID[info.PPID] = append(byPPID[info.PPID], info)
	}

	var descendants []*LiveProcInfo
	visited := map[int]bool{rootPID: true}
	queue := []int{rootPID}
	for len(queue) > 0 {
		cur := queue[0]
		queue = queue[1:]
		for _, child := range byPPID[cur] {
			if visited[child.PID] {
				continue
			}
			visited[child.PID] = true
			descendants = append(descendants, child)
			queue = append(queue, child.PID)
		}
	}
	return descendants, nil
}

func (osProcessBackend) SendSignalVerified(pid int, expectedTicks uint64, expectedUID int, sig syscall.Signal) error {
	pidfd, err := unix.PidfdOpen(pid, 0)
	if err == nil {
		defer func() { _ = unix.Close(pidfd) }()
		// Đối chiếu lại danh tính PID trong khi đang nắm giữ pidfd để chống TOCTOU PID reuse
		live, inspectErr := readLiveProc(pid)
		if inspectErr != nil {
			return fmt.Errorf("PROCESS_NOT_FOUND: %w", inspectErr)
		}
		if live.StartTimeTicks != expectedTicks {
			return fmt.Errorf("PID_RECYCLED: PID %d starttime ticks thay đổi (%d != %d)", pid, live.StartTimeTicks, expectedTicks)
		}
		if live.UID != expectedUID {
			return fmt.Errorf("PERMISSION_DENIED: UID tiến trình %d thay đổi (%d != %d)", pid, live.UID, expectedUID)
		}
		if sigErr := unix.PidfdSendSignal(pidfd, sig, nil, 0); sigErr != nil {
			return fmt.Errorf("SIGNAL_FAILED: %w", sigErr)
		}
		return nil
	}

	if errors.Is(err, unix.ENOSYS) {
		live, inspectErr := readLiveProc(pid)
		if inspectErr != nil {
			return fmt.Errorf("PROCESS_NOT_FOUND: %w", inspectErr)
		}
		if live.StartTimeTicks != expectedTicks {
			return fmt.Errorf("PID_RECYCLED: PID %d starttime ticks thay đổi", pid)
		}
		if live.UID != expectedUID {
			return fmt.Errorf("PERMISSION_DENIED: UID tiến trình %d thay đổi", pid)
		}
		if sigErr := syscall.Kill(pid, sig); sigErr != nil {
			return fmt.Errorf("SIGNAL_FAILED: %w", sigErr)
		}
		return nil
	}

	if errors.Is(err, syscall.ESRCH) {
		return fmt.Errorf("PROCESS_NOT_FOUND: %w", err)
	}
	if errors.Is(err, syscall.EPERM) || errors.Is(err, syscall.EACCES) {
		return fmt.Errorf("PERMISSION_DENIED: %w", err)
	}
	return fmt.Errorf("PIDFD_OPEN_FAILED: %w", err)
}

// Controller điều khiển dừng/buộc dừng tiến trình an toàn (pidfd + per-descendant + post-verify).
type Controller struct {
	collector    *Collector
	serverID     string
	backend      ProcessBackend
	inFlightMu   sync.Mutex
	inFlightRefs map[string]*sync.Mutex
}

// NewController khởi tạo process controller dùng OS /proc + pidfd backend.
func NewController(collector *Collector) *Controller {
	srvID := ""
	if collector != nil {
		srvID = collector.serverID
	}
	return &Controller{
		collector:    collector,
		serverID:     srvID,
		backend:      osProcessBackend{},
		inFlightRefs: make(map[string]*sync.Mutex),
	}
}

// NewControllerWithBackend khởi tạo process controller với backend giả lập phục vụ kiểm thử.
func NewControllerWithBackend(collector *Collector, serverID string, backend ProcessBackend) *Controller {
	if backend == nil {
		backend = osProcessBackend{}
	}
	if serverID == "" && collector != nil {
		serverID = collector.serverID
	}
	return &Controller{
		collector:    collector,
		serverID:     serverID,
		backend:      backend,
		inFlightRefs: make(map[string]*sync.Mutex),
	}
}

func (ctrl *Controller) lockRef(ref string) func() {
	ctrl.inFlightMu.Lock()
	mu, exists := ctrl.inFlightRefs[ref]
	if !exists {
		mu = &sync.Mutex{}
		ctrl.inFlightRefs[ref] = mu
	}
	ctrl.inFlightMu.Unlock()
	mu.Lock()
	return mu.Unlock
}

func isExitedLive(live *LiveProcInfo, err error, expectedTicks uint64) bool {
	if err != nil || live == nil {
		return true
	}
	if live.StartTimeTicks != expectedTicks {
		return true
	}
	return live.State == "Z"
}

// TerminateProcess gửi tín hiệu SIGTERM an toàn (admin/control-plane scope).
func (ctrl *Controller) TerminateProcess(ref string, timeoutSec int, terminateTree bool) (*ControlResult, error) {
	return ctrl.TerminateProcessAuthorized(ref, "", true, timeoutSec, terminateTree)
}

// TerminateProcessAuthorized gửi SIGTERM kèm xác minh quyền actor và hậu kiểm toàn bộ cây tiến trình.
func (ctrl *Controller) TerminateProcessAuthorized(ref, actor string, isAdmin bool, timeoutSec int, terminateTree bool) (*ControlResult, error) {
	if timeoutSec <= 0 {
		timeoutSec = 10
	}
	return ctrl.executeControl(ref, actor, isAdmin, syscall.SIGTERM, "SIGTERM", timeoutSec, terminateTree)
}

// KillProcess gửi tín hiệu SIGKILL cưỡng chế kèm xác nhận force=true và hậu kiểm bắt buộc.
func (ctrl *Controller) KillProcess(ref string, force bool, killTree bool) (*ControlResult, error) {
	return ctrl.KillProcessAuthorized(ref, "", true, force, killTree)
}

// KillProcessAuthorized gửi SIGKILL kèm kiểm tra actor và hậu kiểm bắt buộc (không báo KILLED khi còn sống).
func (ctrl *Controller) KillProcessAuthorized(ref, actor string, isAdmin, force, killTree bool) (*ControlResult, error) {
	if !force {
		return nil, fmt.Errorf("FORCE_CONFIRMATION_REQUIRED: Buộc dừng (SIGKILL) bắt buộc phải có cờ force: true")
	}
	return ctrl.executeControl(ref, actor, isAdmin, syscall.SIGKILL, "SIGKILL", 1, killTree)
}

func (ctrl *Controller) executeControl(
	ref string,
	actor string,
	isAdmin bool,
	sig syscall.Signal,
	actionName string,
	waitSeconds int,
	includeTree bool,
) (*ControlResult, error) {
	parsedRef, err := ParseRef(ref)
	if err != nil {
		return nil, fmt.Errorf("INVALID_PROCESS_REF: %w", err)
	}
	if parsedRef.PID <= 1 {
		return nil, fmt.Errorf("PROTECTED_PROCESS_DENIED: từ chối can thiệp: PID %d là tiến trình khởi tạo gốc (init/systemd)", parsedRef.PID)
	}
	if ctrl.serverID != "" && parsedRef.ServerID != ctrl.serverID {
		return nil, fmt.Errorf("HOST_MISMATCH: ref nhắm tới node '%s' nhưng agent hiện tại là '%s'", parsedRef.ServerID, ctrl.serverID)
	}

	unlock := ctrl.lockRef(ref)
	defer unlock()

	// 1. Đọc TRỰC TIẾP trạng thái live (không dùng 1.5s cache, không fallback struct rỗng)
	rootLive, err := ctrl.backend.InspectPID(parsedRef.PID)
	if err != nil {
		return nil, fmt.Errorf("PROCESS_NOT_FOUND: Tiến trình PID %d không tồn tại hoặc đã kết thúc (%w)", parsedRef.PID, err)
	}

	// 2. Chống PID reuse bằng StartTimeTicks thực tế
	if rootLive.StartTimeTicks != parsedRef.StartTimeTicks {
		return nil, fmt.Errorf("PID_RECYCLED: PID %d đã bị tái cấp phát cho tiến trình khác (kỳ vọng ticks: %d, thực tế: %d)",
			parsedRef.PID, parsedRef.StartTimeTicks, rootLive.StartTimeTicks)
	}

	// 3. Đối chiếu cgroup/container binding thực tế với container trong ProcessRef
	actualContainer := rootLive.ContainerID
	if actualContainer == "" {
		actualContainer = "host"
	}
	if actualContainer != parsedRef.ContainerID {
		return nil, fmt.Errorf("CONTAINER_MISMATCH: container thực tế '%s' không khớp với ProcessRef '%s'", actualContainer, parsedRef.ContainerID)
	}

	// 4. Kiểm tra Safeguard danh sách bảo vệ trên snapshot đầy đủ
	if err := CheckSafeGuard(rootLive.toProcessInfo()); err != nil {
		return nil, fmt.Errorf("PROTECTED_PROCESS_DENIED: %w", err)
	}

	// 5. Nếu không phải Admin, kiểm tra owner tiến trình khớp actor đã xác thực
	if !isAdmin {
		if strings.TrimSpace(actor) == "" || !strings.EqualFold(rootLive.User, strings.TrimSpace(actor)) {
			return nil, fmt.Errorf("PERMISSION_DENIED: tiến trình PID %d thuộc '%s', không thuộc '%s'", rootLive.PID, rootLive.User, actor)
		}
	}

	// 6. Thu thập cây tiến trình con và xác minh TỪNG descendant (tuyệt đối KHÔNG dùng syscall.Kill(-pgid))
	type targetProc struct {
		PID   int
		Ticks uint64
		UID   int
		Name  string
	}
	targets := []targetProc{{PID: rootLive.PID, Ticks: rootLive.StartTimeTicks, UID: rootLive.UID, Name: rootLive.Name}}
	var skippedDescendants []string

	if includeTree {
		children, cErr := ctrl.backend.ListChildrenPIDs(rootLive.PID)
		if cErr == nil {
			for _, child := range children {
				if child.PID == rootLive.PID || child.PID <= 2 {
					continue
				}
				if child.UID != rootLive.UID {
					skippedDescendants = append(skippedDescendants, fmt.Sprintf("pid=%d owner_mismatch(%s!=%s)", child.PID, child.User, rootLive.User))
					continue
				}
				childContainer := child.ContainerID
				if childContainer == "" {
					childContainer = "host"
				}
				if childContainer != actualContainer {
					skippedDescendants = append(skippedDescendants, fmt.Sprintf("pid=%d container_mismatch(%s!=%s)", child.PID, childContainer, actualContainer))
					continue
				}
				if sgErr := CheckSafeGuard(child.toProcessInfo()); sgErr != nil {
					skippedDescendants = append(skippedDescendants, fmt.Sprintf("pid=%d protected(%s)", child.PID, child.Name))
					continue
				}
				targets = append(targets, targetProc{PID: child.PID, Ticks: child.StartTimeTicks, UID: child.UID, Name: child.Name})
			}
		}
	}

	// 7. Gửi tín hiệu lần lượt từng PID đã xác minh (từ con tới gốc) và bắt toàn bộ lỗi kernel
	var signaled []int
	signalErrors := make(map[int]string)

	for i := len(targets) - 1; i >= 0; i-- {
		t := targets[i]
		sigErr := ctrl.backend.SendSignalVerified(t.PID, t.Ticks, t.UID, sig)
		if sigErr != nil {
			signalErrors[t.PID] = sigErr.Error()
			if t.PID == rootLive.PID && (errors.Is(sigErr, syscall.EPERM) || strings.Contains(sigErr.Error(), "PERMISSION_DENIED") || strings.Contains(sigErr.Error(), "EPERM") || strings.Contains(sigErr.Error(), "operation not permitted")) {
				return nil, fmt.Errorf("PERMISSION_DENIED: không có quyền gửi tín hiệu tới PID %d (%v)", rootLive.PID, sigErr)
			}
			continue
		}
		signaled = append(signaled, t.PID)
	}

	// 8. Hậu kiểm bắt buộc toàn bộ target PIDs (áp dụng cho cả SIGTERM lẫn SIGKILL)
	pollBudget := time.Duration(waitSeconds) * time.Second
	if actionName == "SIGKILL" {
		pollBudget = 300 * time.Millisecond
	}
	deadline := time.Now().Add(pollBudget)

	var killed []int
	var remaining []int
	for {
		killed = killed[:0]
		remaining = remaining[:0]
		for _, t := range targets {
			liveAfter, afterErr := ctrl.backend.InspectPID(t.PID)
			if isExitedLive(liveAfter, afterErr, t.Ticks) {
				killed = append(killed, t.PID)
			} else {
				remaining = append(remaining, t.PID)
			}
		}
		if len(remaining) == 0 || !time.Now().Before(deadline) {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}

	now := time.Now().UTC()
	rootStillAlive := false
	for _, rpid := range remaining {
		if rpid == rootLive.PID {
			rootStillAlive = true
			break
		}
	}

	if rootStillAlive {
		status := "TIMEOUT_STILL_RUNNING"
		msg := fmt.Sprintf("Tiến trình PID %d vẫn đang chạy sau %d giây ân hạn.", rootLive.PID, waitSeconds)
		if actionName == "SIGKILL" {
			status = "FAILED_STILL_RUNNING"
			msg = fmt.Sprintf("Buộc dừng SIGKILL PID %d chưa thành công: tiến trình vẫn tồn tại trong bảng kernel (có thể kẹt I/O trạng thái D).", rootLive.PID)
		}
		return &ControlResult{
			Status:             status,
			Action:             actionName,
			Verification:       "STILL_RUNNING",
			Ref:                ref,
			PID:                rootLive.PID,
			Message:            msg,
			ExecutedAt:         now,
			PIDsSignaled:       signaled,
			PIDsKilled:         killed,
			PIDsRemaining:      remaining,
			SkippedDescendants: skippedDescendants,
			SignalErrors:       nonEmptyErrMap(signalErrors),
		}, nil
	}

	if len(remaining) > 0 || len(signalErrors) > 0 || len(skippedDescendants) > 0 {
		return &ControlResult{
			Status:             "PARTIAL_FAILURE",
			Action:             actionName,
			Verification:       "PARTIAL_FAILURE",
			Ref:                ref,
			PID:                rootLive.PID,
			Message:            fmt.Sprintf("Tiến trình gốc PID %d đã dừng, nhưng còn %d tiến trình con hoặc lỗi tín hiệu (%d skipped).", rootLive.PID, len(remaining), len(skippedDescendants)),
			ExecutedAt:         now,
			PIDsSignaled:       signaled,
			PIDsKilled:         killed,
			PIDsRemaining:      remaining,
			SkippedDescendants: skippedDescendants,
			SignalErrors:       nonEmptyErrMap(signalErrors),
		}, nil
	}

	successStatus := "TERMINATED"
	successMsg := fmt.Sprintf("Tiến trình PID %d (%s) đã dừng an toàn và được xác minh.", rootLive.PID, rootLive.Name)
	if actionName == "SIGKILL" {
		successStatus = "KILLED"
		successMsg = fmt.Sprintf("Tiến trình PID %d (%s) đã bị buộc dừng và xác minh thoát hoàn toàn.", rootLive.PID, rootLive.Name)
	}
	return &ControlResult{
		Status:       successStatus,
		Action:       actionName,
		Verification: "VERIFIED",
		Ref:          ref,
		PID:          rootLive.PID,
		Message:      successMsg,
		ExecutedAt:   now,
		PIDsSignaled: signaled,
		PIDsKilled:   killed,
	}, nil
}

func nonEmptyErrMap(m map[int]string) map[int]string {
	if len(m) == 0 {
		return nil
	}
	return m
}
