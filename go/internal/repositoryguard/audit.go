package repositoryguard

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

type AuditLogger struct {
	filePath string
	mu       sync.Mutex
}

func NewAuditLogger(logPath string) (*AuditLogger, error) {
	if err := os.MkdirAll(filepath.Dir(logPath), 0750); err != nil {
		return nil, err
	}
	return &AuditLogger{filePath: logPath}, nil
}

func (al *AuditLogger) LogEvent(actor, action, targetType, targetID, result, details string) {
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
		return
	}

	f, err := os.OpenFile(al.filePath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0640)
	if err != nil {
		return
	}
	defer f.Close()

	_, _ = f.Write(append(data, '\n'))
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
