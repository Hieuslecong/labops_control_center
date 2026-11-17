package agent

import (
	"encoding/json"
	"testing"

	"github.com/Hieuslecong/labops_control_center/go/internal/gpu"
	"github.com/Hieuslecong/labops_control_center/go/internal/host"
)

func TestTelemetryConversionPreservesVerificationAndSourceMetadata(t *testing.T) {
	hostReport := &host.HostTelemetry{
		Status:      "degraded",
		CollectedAt: "2026-11-17T02:00:00Z",
		IPAddress:   "192.0.2.20",
		DiskType:    "ext4 (test-device)",
		IsRealHost:  true,
	}
	hostBytes, err := json.Marshal(toProtoHost("track", hostReport))
	if err != nil {
		t.Fatal(err)
	}
	var hostPayload map[string]any
	if err := json.Unmarshal(hostBytes, &hostPayload); err != nil {
		t.Fatal(err)
	}
	for key, want := range map[string]any{
		"status": "degraded", "ip_address": "192.0.2.20", "disk_type": "ext4 (test-device)", "is_real_host": true,
	} {
		if hostPayload[key] != want {
			t.Errorf("host telemetry %s=%v, want %v", key, hostPayload[key], want)
		}
	}

	gpuReport := &gpu.SystemGPUReport{
		Status:      "DEGRADED",
		CollectedAt: "2026-11-17T02:00:00Z",
		CUDAVersion: "12.4",
		Source:      "labops-go-agent",
		Count:       1,
		GPUs: []gpu.GPUTelemetry{{
			Index: 0, UUID: "GPU-test", PCIBusID: "0000:01:00.0", Name: "NVIDIA A30",
		}},
	}
	gpuBytes, err := json.Marshal(toProtoGPU("track", gpuReport))
	if err != nil {
		t.Fatal(err)
	}
	var gpuPayload map[string]any
	if err := json.Unmarshal(gpuBytes, &gpuPayload); err != nil {
		t.Fatal(err)
	}
	for key, want := range map[string]any{"status": "DEGRADED", "cuda_version": "12.4", "source": "labops-go-agent"} {
		if gpuPayload[key] != want {
			t.Errorf("GPU telemetry %s=%v, want %v", key, gpuPayload[key], want)
		}
	}
	items, ok := gpuPayload["gpus"].([]any)
	if !ok || len(items) != 1 || items[0].(map[string]any)["pci_bus_id"] != "0000:01:00.0" {
		t.Errorf("GPU PCI identity was not preserved: %v", gpuPayload["gpus"])
	}
}
