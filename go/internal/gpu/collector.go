package gpu

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"time"
)

// GPUProcess lưu tiến trình đang dùng VRAM trên GPU.
type GPUProcess struct {
	PID           int    `json:"pid"`
	ProcessName   string `json:"name"`
	UsedMemoryMB  uint64 `json:"used_memory_mb"`
	ContainerName string `json:"container_name,omitempty"`
	Username      string `json:"username,omitempty"`
}

// GPUTelemetry chứa số liệu quan sát trực tiếp từ NVIDIA-SMI.
type GPUTelemetry struct {
	Index             int          `json:"index"`
	UUID              string       `json:"uuid"`
	Name              string       `json:"name"`
	PCIBusID          string       `json:"pci_bus_id"`
	MemoryTotalMB     uint64       `json:"memory_total_mb"`
	MemoryUsedMB      uint64       `json:"memory_used_mb"`
	MemoryFreeMB      uint64       `json:"memory_free_mb"`
	UtilizationGPUPct int          `json:"utilization_gpu_pct"`
	UtilizationMemPct int          `json:"utilization_mem_pct"`
	TemperatureC      int          `json:"temperature_c"`
	PowerWatts        int          `json:"power_watts"`
	PowerLimitWatts   int          `json:"power_limit_watts"`
	ActiveProcesses   []GPUProcess `json:"active_processes"`
	EnforcementState  string       `json:"enforcement_state"`
}

// SystemGPUReport báo cáo toàn bộ hệ thống GPU.
type SystemGPUReport struct {
	Source        string         `json:"source"`
	CollectedAt   string         `json:"collected_at"`
	Status        string         `json:"status"`
	DriverVersion string         `json:"driver_version"`
	CUDAVersion   string         `json:"cuda_version"`
	Count         int            `json:"count"`
	GPUs          []GPUTelemetry `json:"gpus"`
}

// Collector quản lý thu thập số liệu từ NVIDIA-SMI.
type Collector struct {
	mu            sync.RWMutex
	driverVersion string
	cudaVersion   string
	latestReport  *SystemGPUReport
	lastPollTime  time.Time
}

func NewCollector() *Collector {
	c := &Collector{}
	c.initStaticDriverVersions()
	c.latestReport = c.queryNvidiaMetrics()
	return c
}

func (c *Collector) initStaticDriverVersions() {
	if out, err := exec.Command("nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader").Output(); err == nil {
		c.driverVersion = parseDriverVersion(string(out))
	}
	if out, err := exec.Command("nvidia-smi").Output(); err == nil {
		c.cudaVersion = parseCUDAVersion(string(out))
	}
}

func parseDriverVersion(output string) string {
	for _, line := range strings.Split(strings.TrimSpace(output), "\n") {
		if value := strings.TrimSpace(line); value != "" {
			return value
		}
	}
	return ""
}

func parseCUDAVersion(output string) string {
	for _, line := range strings.Split(output, "\n") {
		if _, value, found := strings.Cut(line, "CUDA Version:"); found {
			fields := strings.Fields(strings.Trim(strings.TrimSpace(value), "|"))
			if len(fields) > 0 {
				return fields[0]
			}
		}
	}
	return ""
}

func parseGPUCSVLine(line string) (GPUTelemetry, error) {
	parts := strings.Split(line, ",")
	if len(parts) < 12 {
		return GPUTelemetry{}, fmt.Errorf("GPU telemetry row has %d fields; expected 12", len(parts))
	}
	values := make([]string, len(parts))
	for i, part := range parts {
		values[i] = strings.TrimSpace(part)
	}
	index, err := strconv.Atoi(values[0])
	if err != nil || index < 0 {
		return GPUTelemetry{}, fmt.Errorf("invalid GPU index")
	}
	memTotal, err := strconv.ParseUint(values[4], 10, 64)
	if err != nil || memTotal == 0 {
		return GPUTelemetry{}, fmt.Errorf("invalid GPU memory total")
	}
	memUsed, err := strconv.ParseUint(values[5], 10, 64)
	if err != nil || memUsed > memTotal {
		return GPUTelemetry{}, fmt.Errorf("invalid GPU memory used")
	}
	memFree, err := strconv.ParseUint(values[6], 10, 64)
	if err != nil || memFree > memTotal {
		return GPUTelemetry{}, fmt.Errorf("invalid GPU memory free")
	}
	utilGPU, err := strconv.Atoi(values[7])
	if err != nil || utilGPU < 0 || utilGPU > 100 {
		return GPUTelemetry{}, fmt.Errorf("invalid GPU utilization")
	}
	utilMem, err := strconv.Atoi(values[8])
	if err != nil || utilMem < 0 || utilMem > 100 {
		return GPUTelemetry{}, fmt.Errorf("invalid GPU memory utilization")
	}
	temp, err := strconv.Atoi(values[9])
	if err != nil || temp < 0 {
		return GPUTelemetry{}, fmt.Errorf("invalid GPU temperature")
	}
	powerDraw, err := strconv.ParseFloat(values[10], 64)
	if err != nil || powerDraw < 0 {
		return GPUTelemetry{}, fmt.Errorf("invalid GPU power draw")
	}
	powerLimit, err := strconv.ParseFloat(values[11], 64)
	if err != nil || powerLimit < 0 {
		return GPUTelemetry{}, fmt.Errorf("invalid GPU power limit")
	}
	if values[1] == "" || values[2] == "" {
		return GPUTelemetry{}, fmt.Errorf("GPU UUID and model are required")
	}
	return GPUTelemetry{
		Index: index, UUID: values[1], Name: values[2], PCIBusID: values[3],
		MemoryTotalMB: memTotal, MemoryUsedMB: memUsed, MemoryFreeMB: memFree,
		UtilizationGPUPct: utilGPU, UtilizationMemPct: utilMem, TemperatureC: temp,
		PowerWatts: int(powerDraw), PowerLimitWatts: int(powerLimit),
		ActiveProcesses: []GPUProcess{}, EnforcementState: "MONITOR_ONLY",
	}, nil
}

func (c *Collector) queryNvidiaMetrics() *SystemGPUReport {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	queryArgs := []string{
		"--query-gpu=index,uuid,name,pci.bus_id,memory.total,memory.used,memory.free,utilization.gpu,utilization.memory,temperature.gpu,power.draw,power.limit",
		"--format=csv,noheader,nounits",
	}
	out, err := exec.CommandContext(ctx, "nvidia-smi", queryArgs...).Output()
	if err != nil {
		return &SystemGPUReport{
			Source: "labops-go-agent", CollectedAt: time.Now().UTC().Format(time.RFC3339),
			Status: "UNAVAILABLE", DriverVersion: c.driverVersion, CUDAVersion: c.cudaVersion,
			GPUs: []GPUTelemetry{},
		}
	}

	gpus := make([]GPUTelemetry, 0, 4)
	seenUUIDs, seenIndexes := make(map[string]bool), make(map[int]bool)
	degraded := false
	scanner := bufio.NewScanner(bytes.NewReader(out))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		gpu, parseErr := parseGPUCSVLine(line)
		if parseErr != nil || seenUUIDs[gpu.UUID] || seenIndexes[gpu.Index] {
			degraded = true
			continue
		}
		seenUUIDs[gpu.UUID], seenIndexes[gpu.Index] = true, true
		gpus = append(gpus, gpu)
	}
	if scanner.Err() != nil || len(gpus) == 0 {
		degraded = true
	}

	procCmd := exec.CommandContext(ctx, "nvidia-smi", "--query-compute-apps=gpu_uuid,pid,process_name,used_memory", "--format=csv,noheader,nounits")
	procOut, procErr := procCmd.Output()
	if procErr != nil {
		degraded = true
	} else {
		procScanner := bufio.NewScanner(bytes.NewReader(procOut))
		for procScanner.Scan() {
			line := strings.TrimSpace(procScanner.Text())
			if line == "" {
				continue
			}
			parts := strings.Split(line, ",")
			if len(parts) < 4 {
				degraded = true
				continue
			}
			gpuUUID := strings.TrimSpace(parts[0])
			pid, pidErr := strconv.Atoi(strings.TrimSpace(parts[1]))
			name := strings.TrimSpace(parts[2])
			memory, memoryErr := strconv.ParseUint(strings.TrimSpace(parts[3]), 10, 64)
			if gpuUUID == "" || pidErr != nil || pid <= 0 || name == "" || memoryErr != nil {
				degraded = true
				continue
			}
			matched := false
			for i := range gpus {
				if gpus[i].UUID == gpuUUID {
					gpus[i].ActiveProcesses = append(gpus[i].ActiveProcesses, GPUProcess{PID: pid, ProcessName: name, UsedMemoryMB: memory})
					matched = true
					break
				}
			}
			if !matched {
				degraded = true
			}
		}
		if procScanner.Err() != nil {
			degraded = true
		}
	}

	status := "VERIFIED"
	if degraded {
		status = "DEGRADED"
	}
	return &SystemGPUReport{
		Source: "labops-go-agent", CollectedAt: time.Now().UTC().Format(time.RFC3339),
		Status: status, DriverVersion: c.driverVersion, CUDAVersion: c.cudaVersion,
		Count: len(gpus), GPUs: gpus,
	}
}

func (c *Collector) Collect() *SystemGPUReport {
	c.mu.Lock()
	defer c.mu.Unlock()
	if time.Since(c.lastPollTime) < 3*time.Second && c.latestReport != nil {
		return c.latestReport
	}
	c.latestReport = c.queryNvidiaMetrics()
	c.lastPollTime = time.Now()
	return c.latestReport
}

func (c *Collector) GetLatest() *SystemGPUReport {
	c.mu.RLock()
	latest := c.latestReport
	c.mu.RUnlock()
	if latest == nil {
		return c.Collect()
	}
	return latest
}
