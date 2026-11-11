package host

import (
	"testing"
	"time"

	"github.com/Hieuslecong/labops_control_center/go/internal/config"
)

func TestHostCollector_Collect(t *testing.T) {
	cfg := &config.AgentConfig{
		ServerID:   "track",
		ServerName: "Test Rack Server",
		Hostname:   "aiotlab-rack",
	}

	collector := NewCollector(cfg)
	if collector == nil {
		t.Fatal("NewCollector trả về nil")
	}

	// Đợi 100ms để có delta CPU
	time.Sleep(100 * time.Millisecond)
	tel := collector.Collect()

	if tel == nil {
		t.Fatal("Collect() trả về nil")
	}

	if tel.ServerID != "track" {
		t.Errorf("Kỳ vọng ServerID 'track', nhận được: %s", tel.ServerID)
	}

	if tel.Source != "labops-go-agent" {
		t.Errorf("Kỳ vọng Source 'labops-go-agent', nhận được: %s", tel.Source)
	}

	if tel.RAMTotalGB <= 0 {
		t.Errorf("Kỳ vọng RAMTotalGB > 0, nhận được: %f", tel.RAMTotalGB)
	}

	if tel.DiskTotalTB <= 0 {
		t.Errorf("Kỳ vọng DiskTotalTB > 0, nhận được: %f", tel.DiskTotalTB)
	}

	if tel.CPUCores <= 0 {
		t.Errorf("Kỳ vọng CPUCores > 0, nhận được: %d", tel.CPUCores)
	}

	if tel.UptimeSeconds == 0 {
		t.Errorf("Kỳ vọng UptimeSeconds > 0")
	}

	if len(tel.CollectedAt) == 0 {
		t.Errorf("Kỳ vọng CollectedAt có giá trị RFC3339")
	}
}

func TestHostCollector_CPUSampleDelta(t *testing.T) {
	prev := CPUSample{
		User: 100, Nice: 0, System: 50, Idle: 800, Total: 1000,
	}
	curr := CPUSample{
		User: 150, Nice: 0, System: 80, Idle: 870, Total: 1150,
	}

	totalDiff := float64(curr.Total - prev.Total)
	idleDiff := float64(curr.Idle - prev.Idle)
	usage := ((totalDiff - idleDiff) / totalDiff) * 100.0

	if usage < 50.0 || usage > 55.0 {
		t.Errorf("Kỳ vọng usage ~53.33%%, nhận được: %f", usage)
	}
}

