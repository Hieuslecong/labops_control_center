package gpu

import (
	"bufio"
	"bytes"
	"context"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"time"
)

// GPUProcess lưu tiến trình đang dùng VRAM trên GPU
type GPUProcess struct {
	PID           int    `json:"pid"`
	ProcessName   string `json:"name"`
	UsedMemoryMB  uint64 `json:"used_memory_mb"`
	ContainerName string `json:"container_name,omitempty"`
	Username      string `json:"username,omitempty"`
}

// GPUTelemetry chứa thông tin chi tiết của 1 card GPU NVIDIA A30
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
	EnforcementState  string       `json:"enforcement_state"` // HARDWARE_ENFORCED, MONITOR_ONLY
}

// SystemGPUReport báo cáo toàn bộ hệ thống GPU
type SystemGPUReport struct {
	Source        string         `json:"source"`
	CollectedAt   string         `json:"collected_at"`
	Status        string         `json:"status"`
	DriverVersion string         `json:"driver_version"`
	CUDAVersion   string         `json:"cuda_version"`
	Count         int            `json:"count"`
	GPUs          []GPUTelemetry `json:"gpus"`
}

// Collector quản lý việc thu thập định kỳ thông số GPU từ NVIDIA-SMI
type Collector struct {
	mu            sync.RWMutex
	driverVersion string
	cudaVersion   string
	latestReport  *SystemGPUReport
	lastPollTime  time.Time
}

// NewCollector khởi tạo GPU Collector
func NewCollector() *Collector {
	c := &Collector{}
	c.initStaticDriverVersions()
	c.latestReport = c.queryNvidiaMetrics()
	return c
}

func (c *Collector) initStaticDriverVersions() {
	cmd := exec.Command("nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader")
	if out, err := cmd.Output(); err == nil {
		lines := strings.Split(strings.TrimSpace(string(out)), "\n")
		if len(lines) > 0 {
			c.driverVersion = strings.TrimSpace(lines[0])
		}
	}
	if c.driverVersion == "" {
		c.driverVersion = "550.120"
	}
	c.cudaVersion = "12.4"
}

// queryNvidiaMetrics thực thi một lệnh truy vấn tổng hợp duy nhất tới nvidia-smi
func (c *Collector) queryNvidiaMetrics() *SystemGPUReport {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	// Truy vấn các chỉ số GPU dạng CSV
	queryArgs := []string{
		"--query-gpu=index,uuid,name,pci.bus_id,memory.total,memory.used,memory.free,utilization.gpu,utilization.memory,temperature.gpu,power.draw,power.limit",
		"--format=csv,noheader,nounits",
	}

	cmd := exec.CommandContext(ctx, "nvidia-smi", queryArgs...)
	out, err := cmd.Output()
	if err != nil {
		return &SystemGPUReport{
			Source:        "labops-go-agent",
			CollectedAt:   time.Now().UTC().Format(time.RFC3339),
			Status:        "UNAVAILABLE",
			DriverVersion: c.driverVersion,
			CUDAVersion:   c.cudaVersion,
			Count:         0,
			GPUs:          []GPUTelemetry{},
		}
	}

	gpus := make([]GPUTelemetry, 0, 4)
	scanner := bufio.NewScanner(bytes.NewReader(out))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		parts := strings.Split(line, ",")
		if len(parts) < 12 {
			continue
		}

		idx, _ := strconv.Atoi(strings.TrimSpace(parts[0]))
		uuid := strings.TrimSpace(parts[1])
		name := strings.TrimSpace(parts[2])
		pci := strings.TrimSpace(parts[3])
		memTot, _ := strconv.ParseUint(strings.TrimSpace(parts[4]), 10, 64)
		memUsed, _ := strconv.ParseUint(strings.TrimSpace(parts[5]), 10, 64)
		memFree, _ := strconv.ParseUint(strings.TrimSpace(parts[6]), 10, 64)
		utilGpu, _ := strconv.Atoi(strings.TrimSpace(parts[7]))
		utilMem, _ := strconv.Atoi(strings.TrimSpace(parts[8]))
		temp, _ := strconv.Atoi(strings.TrimSpace(parts[9]))
		pwrDrawFloat, _ := strconv.ParseFloat(strings.TrimSpace(parts[10]), 64)
		pwrLimitFloat, _ := strconv.ParseFloat(strings.TrimSpace(parts[11]), 64)

		g := GPUTelemetry{
			Index:             idx,
			UUID:              uuid,
			Name:              name,
			PCIBusID:          pci,
			MemoryTotalMB:     memTot,
			MemoryUsedMB:      memUsed,
			MemoryFreeMB:      memFree,
			UtilizationGPUPct: utilGpu,
			UtilizationMemPct: utilMem,
			TemperatureC:      temp,
			PowerWatts:        int(pwrDrawFloat),
			PowerLimitWatts:   int(pwrLimitFloat),
			ActiveProcesses:   []GPUProcess{},
			EnforcementState:  "MONITOR_ONLY", // Tuân thủ nguyên tắc Phase 5.3
		}
		gpus = append(gpus, g)
	}

	// Đọc danh sách tiến trình compute đang chạy trên GPU
	procCmd := exec.CommandContext(ctx, "nvidia-smi", "--query-compute-apps=gpu_uuid,pid,process_name,used_memory", "--format=csv,noheader,nounits")
	if procOut, err := procCmd.Output(); err == nil {
		procScanner := bufio.NewScanner(bytes.NewReader(procOut))
		for procScanner.Scan() {
			pLine := strings.TrimSpace(procScanner.Text())
			if pLine == "" {
				continue
			}
			pParts := strings.Split(pLine, ",")
			if len(pParts) >= 4 {
				pUUID := strings.TrimSpace(pParts[0])
				pid, _ := strconv.Atoi(strings.TrimSpace(pParts[1]))
				pName := strings.TrimSpace(pParts[2])
				pMem, _ := strconv.ParseUint(strings.TrimSpace(pParts[3]), 10, 64)

				for i := range gpus {
					if gpus[i].UUID == pUUID {
						gpus[i].ActiveProcesses = append(gpus[i].ActiveProcesses, GPUProcess{
							PID:          pid,
							ProcessName:  pName,
							UsedMemoryMB: pMem,
						})
						break
					}
				}
			}
		}
	}

	return &SystemGPUReport{
		Source:        "labops-go-agent",
		CollectedAt:   time.Now().UTC().Format(time.RFC3339),
		Status:        "VERIFIED",
		DriverVersion: c.driverVersion,
		CUDAVersion:   c.cudaVersion,
		Count:         len(gpus),
		GPUs:          gpus,
	}
}

// Collect thu thập và cập nhật cache nếu thời gian vượt quá chu kỳ tối thiểu (3s)
func (c *Collector) Collect() *SystemGPUReport {
	c.mu.Lock()
	defer c.mu.Unlock()

	// Tránh spam subprocess nếu được gọi nhiều lần trong 3 giây
	if time.Since(c.lastPollTime) < 3*time.Second && c.latestReport != nil {
		return c.latestReport
	}

	c.latestReport = c.queryNvidiaMetrics()
	c.lastPollTime = time.Now()
	return c.latestReport
}

// GetLatest lấy báo cáo GPU gần nhất
func (c *Collector) GetLatest() *SystemGPUReport {
	c.mu.RLock()
	defer c.mu.RUnlock()
	if c.latestReport == nil {
		c.mu.RUnlock()
		return c.Collect()
	}
	return c.latestReport
}
