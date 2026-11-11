package gpu

import (
	"testing"
)

func TestGPUCollector_Collect(t *testing.T) {
	collector := NewCollector()
	if collector == nil {
		t.Fatal("NewCollector trả về nil")
	}

	report := collector.Collect()
	if report == nil {
		t.Fatal("Collect() trả về nil")
	}

	if report.Source != "labops-go-agent" {
		t.Errorf("Kỳ vọng Source 'labops-go-agent', nhận được: %s", report.Source)
	}

	// Trên máy aiotlab-rack thực tế phải có 3 GPU A30
	if report.Status == "VERIFIED" {
		if report.Count != 3 {
			t.Logf("Lưu ý: phát hiện %d GPU trên máy", report.Count)
		}
		for i, g := range report.GPUs {
			if g.Name == "" || g.UUID == "" {
				t.Errorf("GPU %d thiếu tên hoặc UUID: %+v", i, g)
			}
			if g.EnforcementState != "MONITOR_ONLY" {
				t.Errorf("Kỳ vọng EnforcementState 'MONITOR_ONLY', nhận được: %s", g.EnforcementState)
			}
		}
	}
}

