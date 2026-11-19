package resources

import (
	"bufio"
	"math"
	"os"
	"strconv"
	"strings"
	"syscall"
)

// MemoryPolicyManager quản lý việc bảo vệ RAM cấp host và tính toán ngân sách dùng chung.
type MemoryPolicyManager struct {
	hostID            string
	reservePercent    float64
	minReserveBytes   int64
	isCgroupV2        bool
	simulatedTotalRAM int64
}

// NewMemoryPolicyManager khởi tạo bộ quản lý chính sách bộ nhớ.
func NewMemoryPolicyManager(hostID string, reservePercent float64) *MemoryPolicyManager {
	if reservePercent <= 0 {
		reservePercent = 20.0 // Mặc định dành 20% cho host OS và system services
	}
	m := &MemoryPolicyManager{
		hostID:          hostID,
		reservePercent:  reservePercent,
		minReserveBytes: 8 * 1024 * 1024 * 1024, // Tối thiểu 8 GiB
	}
	m.isCgroupV2 = checkCgroupV2()
	return m
}

// SetSimulatedRAM cho phép giả lập tổng RAM trong môi trường test.
func (m *MemoryPolicyManager) SetSimulatedRAM(totalBytes int64) {
	m.simulatedTotalRAM = totalBytes
}

// ContainerMemoryConfig cấu hình HostConfig cho Docker container.
type ContainerMemoryConfig struct {
	Mode              MemoryProtectionMode `json:"mode"`
	MemoryLimitBytes  int64                `json:"memory_limit_bytes"` // Bounded bởi Shared Budget
	MemoryReservation int64                `json:"memory_reservation"` // Soft limit
	MemorySwapBytes   int64                `json:"memory_swap_bytes"`  // Cấm swap bừa bãi
	Description       string               `json:"description"`
}

// EvaluateHostProfile tính toán ngân sách bộ nhớ thực tế của host.
func (m *MemoryPolicyManager) EvaluateHostProfile() HostResourceProfile {
	totalRAM := m.getTotalRAM()
	totalGiB := math.Round(float64(totalRAM)/(1024*1024*1024)*10) / 10

	// Tính vùng dự phòng host: Tối thiểu minReserveBytes hoặc reservePercent%
	reserveBytes := int64(float64(totalRAM) * (m.reservePercent / 100.0))
	if reserveBytes < m.minReserveBytes {
		reserveBytes = m.minReserveBytes
	}
	if reserveBytes > totalRAM/2 {
		reserveBytes = totalRAM / 2 // Không bao giờ vượt quá 50%
	}
	reserveGiB := math.Round(float64(reserveBytes)/(1024*1024*1024)*10) / 10

	sharedBudgetBytes := totalRAM - reserveBytes
	sharedBudgetGiB := math.Round(float64(sharedBudgetBytes)/(1024*1024*1024)*10) / 10

	vStatus := PolicyVerified
	if !m.isCgroupV2 {
		vStatus = PolicyNotVerified
	}

	return HostResourceProfile{
		HostID:             m.hostID,
		TotalCPUCores:      getCPUCores(),
		CPUSchedulingMode:  CPUModeSharedWeighted,
		DefaultCPUWeight:   100,
		TotalRAMBytes:      totalRAM,
		TotalRAMGiB:        totalGiB,
		HostReserveBytes:   reserveBytes,
		HostReserveGiB:     reserveGiB,
		SharedBudgetBytes:  sharedBudgetBytes,
		SharedBudgetGiB:    sharedBudgetGiB,
		MemoryPolicy:       MemoryModeSharedHostProtected,
		PidsLimit:          4096,
		CgroupV2Supported:  m.isCgroupV2,
		PSIMonitoring:      checkPSIAvailable(),
		VerificationStatus: vStatus,
		LastEvaluatedAt:    nowISO(),
	}
}

// BuildContainerConfig sinh cấu hình giới hạn RAM an toàn cho container nghiên cứu.
func (m *MemoryPolicyManager) BuildContainerConfig(mode MemoryProtectionMode) ContainerMemoryConfig {
	if mode == MemoryModeLegacyFixed {
		return ContainerMemoryConfig{
			Mode:              MemoryModeLegacyFixed,
			MemoryLimitBytes:  16 * 1024 * 1024 * 1024,
			MemoryReservation: 8 * 1024 * 1024 * 1024,
			MemorySwapBytes:   16 * 1024 * 1024 * 1024,
			Description:       "Legacy fixed 16 GiB allocation",
		}
	}

	profile := m.EvaluateHostProfile()
	// Container không bị giới hạn cứng 8/16 GiB tùy tiện,
	// nhưng trần tối đa được khóa chặt ở SharedBudgetBytes để bảo vệ Host OS không bị OOM treo máy.
	return ContainerMemoryConfig{
		Mode:              MemoryModeSharedHostProtected,
		MemoryLimitBytes:  profile.SharedBudgetBytes,
		MemoryReservation: 4 * 1024 * 1024 * 1024, // 4 GiB soft reservation
		MemorySwapBytes:   profile.SharedBudgetBytes, // MemorySwap == Memory => Tắt Swap mở rộng
		Description:       "Shared memory budget with host-level reserve protection",
	}
}

// ReadMemoryPressure đọc số liệu áp lực bộ nhớ Linux PSI (/proc/pressure/memory).
func (m *MemoryPolicyManager) ReadMemoryPressure() MemoryPressureTelemetry {
	res := MemoryPressureTelemetry{
		Level: "NORMAL",
	}

	file, err := os.Open("/proc/pressure/memory")
	if err != nil {
		return res
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "some ") {
			fields := strings.Fields(line)
			for _, f := range fields {
				kv := strings.Split(f, "=")
				if len(kv) == 2 {
					val, _ := strconv.ParseFloat(kv[1], 64)
					switch kv[0] {
					case "avg10":
						res.Avg10 = val
					case "avg60":
						res.Avg60 = val
					case "avg300":
						res.Avg300 = val
					case "total":
						tot, _ := strconv.ParseUint(kv[1], 10, 64)
						res.TotalMicros = tot
					}
				}
			}
		}
	}

	if res.Avg10 > 50.0 {
		res.Level = "CRITICAL"
	} else if res.Avg10 > 20.0 {
		res.Level = "WARNING"
	}

	return res
}

func (m *MemoryPolicyManager) getTotalRAM() int64 {
	if m.simulatedTotalRAM > 0 {
		return m.simulatedTotalRAM
	}

	var info syscall.Sysinfo_t
	if err := syscall.Sysinfo(&info); err == nil && info.Totalram > 0 {
		return int64(info.Totalram) * int64(info.Unit)
	}

	// Fallback đọc /proc/meminfo
	if data, err := os.ReadFile("/proc/meminfo"); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			if strings.HasPrefix(line, "MemTotal:") {
				fields := strings.Fields(line)
				if len(fields) >= 2 {
					if kb, errKb := strconv.ParseInt(fields[1], 10, 64); errKb == nil {
						return kb * 1024
					}
				}
			}
		}
	}

	return 64 * 1024 * 1024 * 1024 // 64 GiB default
}

func checkCgroupV2() bool {
	if _, err := os.Stat("/sys/fs/cgroup/cgroup.controllers"); err == nil {
		return true
	}
	return false
}

func checkPSIAvailable() bool {
	if _, err := os.Stat("/proc/pressure/memory"); err == nil {
		return true
	}
	return false
}

func getCPUCores() int {
	return 16 // Default fallback
}
