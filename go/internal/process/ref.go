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
