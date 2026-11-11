package process

import (
	"os"
	"testing"
)

func TestProcessCollector_CollectCurrentProcess(t *testing.T) {
	collector := NewCollector("track")
	report := collector.Collect("all")

	if report == nil {
		t.Fatal("Collect('all') trả về nil")
	}

	if report.TotalCount <= 0 {
		t.Errorf("Kỳ vọng TotalCount > 0, nhận được: %d", report.TotalCount)
	}

	myPID := os.Getpid()
	foundMyPID := false
	for _, p := range report.Processes {
		if p.PID == myPID {
			foundMyPID = true
			if p.User == "" {
				t.Errorf("Tiến trình hiện tại thiếu thông tin người dùng")
			}
			break
		}
	}

	if !foundMyPID {
		t.Errorf("Không tìm thấy chính tiến trình test (PID %d) trong kết quả quét /proc", myPID)
	}
}

