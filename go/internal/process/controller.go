package process

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// Controller điều khiển dừng/buộc dừng tiến trình
type Controller struct {
	collector *Collector
}

// NewController khởi tạo process controller
func NewController(collector *Collector) *Controller {
	return &Controller{
		collector: collector,
	}
}

// readProcessStartTimeTicks đọc ticks khởi tạo thực tế của PID từ /proc/{pid}/stat tại thời điểm microsecond
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
		return 0, 0, "", "", fmt.Errorf("không thể phân tích định dạng /proc/%d/stat", pid)
	}

	name := statStr[firstParen+1 : lastParen]
	afterParen := strings.Fields(statStr[lastParen+2:])
	if len(afterParen) < 20 {
		return 0, 0, "", "", fmt.Errorf("/proc/%d/stat không đủ trường dữ liệu", pid)
	}

	state := afterParen[0]
	pgid, _ := strconv.Atoi(afterParen[2])
	startTimeTicks, err := strconv.ParseUint(afterParen[19], 10, 64)
	if err != nil {
		return 0, 0, "", "", fmt.Errorf("lỗi đọc starttime ticks: %w", err)
	}

	return startTimeTicks, pgid, name, state, nil
}

// TerminateProcess gửi tín hiệu SIGTERM an toàn tới tiến trình hoặc cây tiến trình
func (ctrl *Controller) TerminateProcess(ref string, timeoutSec int, terminateTree bool) (*ControlResult, error) {
	if timeoutSec <= 0 {
		timeoutSec = 10
	}

	parsedRef, err := ParseRef(ref)
	if err != nil {
		return nil, fmt.Errorf("INVALID_PROCESS_REF: %w", err)
	}

	// 1. Kiểm tra pre-flight trên /proc
	realTicks, pgid, procName, _, err := readProcessStartTimeTicks(parsedRef.PID)
	if err != nil {
		return nil, fmt.Errorf("PROCESS_NOT_FOUND: Tiến trình PID %d không tồn tại hoặc đã kết thúc (%w)", parsedRef.PID, err)
	}

	// 2. Chống PID reuse: đối chiếu starttime ticks
	if realTicks != parsedRef.StartTimeTicks {
		return nil, fmt.Errorf("PID_RECYCLED: PID %d đã bị tái cấp phát cho tiến trình khác (kỳ vọng ticks: %d, thực tế: %d). Hủy thao tác vì lý do an toàn.",
			parsedRef.PID, parsedRef.StartTimeTicks, realTicks)
	}

	// 3. Lấy thông tin hiện tại để kiểm tra danh sách bảo vệ
	procInfo, err := ctrl.collector.GetProcessByPID(parsedRef.PID)
	if err != nil {
		// Fallback kiểm tra cơ bản
		procInfo = &ProcessInfo{
			PID:         parsedRef.PID,
			Name:        procName,
			ContainerID: parsedRef.ContainerID,
		}
	}

	if err := CheckSafeGuard(*procInfo); err != nil {
		return nil, fmt.Errorf("PROTECTED_PROCESS_DENIED: %w", err)
	}

	pidsAffected := []int{parsedRef.PID}

	// 4. Nếu terminateTree, tìm danh sách tất cả children
	if terminateTree {
		children := ctrl.collector.GetChildrenPIDs(parsedRef.PID)
		pidsAffected = append(pidsAffected, children...)
	}

	// 5. Gửi SIGTERM tới tiến trình hoặc nhóm tiến trình
	// Nếu terminateTree và pgid == pid, ta có thể gửi tới Process Group (-pgid)
	if terminateTree && pgid == parsedRef.PID && pgid > 1 {
		_ = syscall.Kill(-pgid, syscall.SIGTERM)
	} else {
		for _, p := range pidsAffected {
			_ = syscall.Kill(p, syscall.SIGTERM)
		}
	}

	// 6. Chờ tối đa timeoutSec để kiểm tra xem tiến trình đã kết thúc chưa
	deadline := time.Now().Add(time.Duration(timeoutSec) * time.Second)
	terminated := false
	for time.Now().Before(deadline) {
		time.Sleep(200 * time.Millisecond)
		// Kiểm tra xem PID chính còn tồn tại, cùng ticks và chưa chuyển sang zombie không
		currTicks, _, _, currState, err := readProcessStartTimeTicks(parsedRef.PID)
		if err != nil || currTicks != parsedRef.StartTimeTicks || currState == "Z" {
			terminated = true
			break
		}
	}

	now := time.Now().UTC()
	if terminated {
		return &ControlResult{
			Status:     "TERMINATED",
			Action:     "SIGTERM",
			Ref:        ref,
			PID:        parsedRef.PID,
			Message:    fmt.Sprintf("Tiến trình PID %d (%s) đã dừng an toàn thành công.", parsedRef.PID, procName),
			ExecutedAt: now,
			PIDsKilled: pidsAffected,
		}, nil
	}

	return &ControlResult{
		Status:     "TIMEOUT_STILL_RUNNING",
		Action:     "SIGTERM",
		Ref:        ref,
		PID:        parsedRef.PID,
		Message:    fmt.Sprintf("Tiến trình PID %d vẫn đang chạy sau %d giây ân hạn. Cần xem xét buộc dừng (SIGKILL) nếu cần giải phóng tài nguyên.", parsedRef.PID, timeoutSec),
		ExecutedAt: now,
		PIDsKilled: pidsAffected,
	}, nil
}

// KillProcess gửi tín hiệu SIGKILL cưỡng chế
func (ctrl *Controller) KillProcess(ref string, force bool, killTree bool) (*ControlResult, error) {
	if !force {
		return nil, fmt.Errorf("FORCE_CONFIRMATION_REQUIRED: Buộc dừng (SIGKILL) bắt buộc phải có cờ force: true")
	}

	parsedRef, err := ParseRef(ref)
	if err != nil {
		return nil, fmt.Errorf("INVALID_PROCESS_REF: %w", err)
	}

	realTicks, pgid, procName, _, err := readProcessStartTimeTicks(parsedRef.PID)
	if err != nil {
		return nil, fmt.Errorf("PROCESS_NOT_FOUND: Tiến trình PID %d không tồn tại (%w)", parsedRef.PID, err)
	}

	if realTicks != parsedRef.StartTimeTicks {
		return nil, fmt.Errorf("PID_RECYCLED: PID %d đã bị tái sử dụng. Hủy thao tác buộc dừng để bảo vệ an toàn.", parsedRef.PID)
	}

	procInfo, err := ctrl.collector.GetProcessByPID(parsedRef.PID)
	if err != nil {
		procInfo = &ProcessInfo{
			PID:         parsedRef.PID,
			Name:        procName,
			ContainerID: parsedRef.ContainerID,
		}
	}

	if err := CheckSafeGuard(*procInfo); err != nil {
		return nil, fmt.Errorf("PROTECTED_PROCESS_DENIED: %w", err)
	}

	pidsAffected := []int{parsedRef.PID}
	if killTree {
		children := ctrl.collector.GetChildrenPIDs(parsedRef.PID)
		pidsAffected = append(pidsAffected, children...)
	}

	if killTree && pgid == parsedRef.PID && pgid > 1 {
		_ = syscall.Kill(-pgid, syscall.SIGKILL)
	} else {
		for _, p := range pidsAffected {
			_ = syscall.Kill(p, syscall.SIGKILL)
		}
	}

	time.Sleep(150 * time.Millisecond)

	return &ControlResult{
		Status:     "KILLED",
		Action:     "SIGKILL",
		Ref:        ref,
		PID:        parsedRef.PID,
		Message:    fmt.Sprintf("Tiến trình PID %d (%s) và các tiến trình con đã bị buộc dừng cưỡng chế.", parsedRef.PID, procName),
		ExecutedAt: time.Now().UTC(),
		PIDsKilled: pidsAffected,
	}, nil
}
