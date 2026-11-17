package gpu

import "testing"

func TestParseDriverAndCUDAVersionsDoesNotInventDefaults(t *testing.T) {
	if got := parseDriverVersion("\n"); got != "" {
		t.Fatalf("missing driver version = %q", got)
	}
	if got := parseCUDAVersion("NVIDIA-SMI has no CUDA Version field"); got != "" {
		t.Fatalf("missing CUDA version = %q", got)
	}
	if got := parseDriverVersion("550.163.01\n550.163.01"); got != "550.163.01" {
		t.Fatalf("driver version = %q", got)
	}
	if got := parseCUDAVersion("| NVIDIA-SMI 550.163.01  Driver Version: 550.163.01  CUDA Version: 12.4 |"); got != "12.4" {
		t.Fatalf("CUDA version = %q", got)
	}
}

func TestParseGPUCSVLineRequiresObservedIdentityAndValidMetrics(t *testing.T) {
	line := "0, GPU-test-uuid, NVIDIA Test GPU, 0000:01:00.0, 24000, 2000, 22000, 45, 30, 51, 75.5, 165"
	gpu, err := parseGPUCSVLine(line)
	if err != nil {
		t.Fatal(err)
	}
	if gpu.UUID != "GPU-test-uuid" || gpu.MemoryUsedMB != 2000 || gpu.TemperatureC != 51 || gpu.PowerWatts != 75 {
		t.Fatalf("parsed telemetry mismatch: %+v", gpu)
	}
	for _, invalid := range []string{
		"0, , NVIDIA Test GPU, bus, 24000, 1000, 23000, 10, 10, 40, 10, 165",
		"0, GPU-test-uuid, NVIDIA Test GPU, bus, 24000, N/A, 23000, 10, 10, 40, 10, 165",
		"0, GPU-test-uuid, NVIDIA Test GPU, bus, 24000, 1000, 23000, 101, 10, 40, 10, 165",
	} {
		if _, err := parseGPUCSVLine(invalid); err == nil {
			t.Fatalf("invalid telemetry row accepted: %s", invalid)
		}
	}
}
