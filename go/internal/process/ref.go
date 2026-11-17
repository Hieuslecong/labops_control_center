package process

import (
	"fmt"
	"strconv"
	"strings"
)

// ProcessRefStruct cấu trúc tách nhỏ của ref
type ProcessRefStruct struct {
	ServerID       string
	ContainerID    string
	PID            int
	StartTimeTicks uint64
}

func validateProcessNode(parsed *ProcessRefStruct, serverID string) error {
	if parsed == nil || serverID == "" || parsed.ServerID != serverID {
		return fmt.Errorf("PROCESS_REF_NODE_MISMATCH: ProcessRef không thuộc node này")
	}
	return nil
}

func validateProcessIdentity(parsed *ProcessRefStruct, serverID string, process ProcessInfo) error {
	if err := validateProcessNode(parsed, serverID); err != nil {
		return err
	}
	expectedContainer := parsed.ContainerID
	if expectedContainer == "host" {
		expectedContainer = ""
	}
	if parsed.PID != process.PID || parsed.StartTimeTicks != process.StartTimeTicks || expectedContainer != process.ContainerID {
		return fmt.Errorf("PROCESS_REF_IDENTITY_MISMATCH: ProcessRef không khớp PID, start time hoặc container hiện tại")
	}
	return nil
}

// BuildRef tạo chuỗi ProcessRef an toàn
func BuildRef(serverID, containerID string, pid int, startTimeTicks uint64) string {
	cid := containerID
	if cid == "" {
		cid = "host"
	}
	return fmt.Sprintf("%s:%s:%d:%d", serverID, cid, pid, startTimeTicks)
}

// ParseRef phân tích chuỗi ProcessRef
func ParseRef(ref string) (*ProcessRefStruct, error) {
	parts := strings.Split(ref, ":")
	if len(parts) != 4 {
		return nil, fmt.Errorf("định dạng ref không hợp lệ, kỳ vọng server:container:pid:starttime, nhận: %s", ref)
	}

	pid, err := strconv.Atoi(parts[2])
	if err != nil || pid <= 0 {
		return nil, fmt.Errorf("pid trong ref không hợp lệ: %s", parts[2])
	}

	ticks, err := strconv.ParseUint(parts[3], 10, 64)
	if err != nil {
		return nil, fmt.Errorf("starttime ticks trong ref không hợp lệ: %s", parts[3])
	}

	return &ProcessRefStruct{
		ServerID:       parts[0],
		ContainerID:    parts[1],
		PID:            pid,
		StartTimeTicks: ticks,
	}, nil
}
