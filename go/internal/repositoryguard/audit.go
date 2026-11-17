package repositoryguard

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	"golang.org/x/sys/unix"
)

type AuditLogger struct {
	filePath string
	mu       sync.Mutex
}

func NewAuditLogger(logPath string) (*AuditLogger, error) {
	if logPath == "" {
		return nil, fmt.Errorf("audit log path is required")
	}
	abs, err := filepath.Abs(logPath)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(abs), 0750); err != nil {
		return nil, err
	}
	parent, err := filepath.EvalSymlinks(filepath.Dir(abs))
	if err != nil || parent != filepath.Dir(abs) {
		return nil, fmt.Errorf("audit log directory must be canonical")
	}
	if info, err := os.Lstat(abs); err == nil {
		if !info.Mode().IsRegular() {
			return nil, fmt.Errorf("audit log must be a regular file")
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	return &AuditLogger{filePath: abs}, nil
}

func (al *AuditLogger) LogEvent(actor, action, targetType, targetID, result, details string) error {
	al.mu.Lock()
	defer al.mu.Unlock()

	event := AuditLogEvent{
		ID:         fmt.Sprintf("aud-rep-%d", time.Now().UnixNano()),
		Timestamp:  time.Now(),
		Actor:      actor,
		Action:     action,
		TargetType: targetType,
		TargetID:   targetID,
		Result:     result,
		Details:    details,
	}

	data, err := json.Marshal(event)
	if err != nil {
		return err
	}

	fd, err := unix.Open(al.filePath, unix.O_CREAT|unix.O_WRONLY|unix.O_APPEND|unix.O_CLOEXEC|unix.O_NOFOLLOW, 0640)
	if err != nil {
		return err
	}
	f := os.NewFile(uintptr(fd), al.filePath)
	if _, err := f.Write(append(data, '\n')); err != nil {
		_ = f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		_ = f.Close()
		return err
	}
	return f.Close()
}

func (al *AuditLogger) GetRecentEvents(limit int) []AuditLogEvent {
	al.mu.Lock()
	defer al.mu.Unlock()

	data, err := os.ReadFile(al.filePath)
	if err != nil {
		return nil
	}

	var events []AuditLogEvent
	lines := splitLines(data)
	for i := len(lines) - 1; i >= 0 && len(events) < limit; i-- {
		line := lines[i]
		if len(line) == 0 {
			continue
		}
		var ev AuditLogEvent
		if err := json.Unmarshal(line, &ev); err == nil {
			events = append(events, ev)
		}
	}
	return events
}

func splitLines(data []byte) [][]byte {
	var lines [][]byte
	start := 0
	for i, b := range data {
		if b == '\n' {
			lines = append(lines, data[start:i])
			start = i + 1
		}
	}
	if start < len(data) {
		lines = append(lines, data[start:])
	}
	return lines
}
