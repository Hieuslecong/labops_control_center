package resources

import "runtime"

// CPUPolicyManager điều phối chính sách CPU dùng chung theo trọng số.
type CPUPolicyManager struct {
	defaultWeight int
	pidsLimit     int64
}

// NewCPUPolicyManager khởi tạo bộ điều phối chính sách CPU.
func NewCPUPolicyManager(defaultWeight int, pidsLimit int64) *CPUPolicyManager {
	if defaultWeight <= 0 {
		defaultWeight = 100
	}
	if pidsLimit <= 0 {
		pidsLimit = 4096
	}
	return &CPUPolicyManager{
		defaultWeight: defaultWeight,
		pidsLimit:     pidsLimit,
	}
}

// ContainerCPUConfig chứa các tham số Docker HostConfig sinh ra cho container.
type ContainerCPUConfig struct {
	Mode        CPUSchedulingMode `json:"mode"`
	CpuShares   int64             `json:"cpu_shares"`
	NanoCPUs    int64             `json:"nano_cpus"`
	PidsLimit   int64             `json:"pids_limit"`
	CpusetCpus  string            `json:"cpuset_cpus"`
	Description string            `json:"description"`
}

// BuildContainerConfig sinh cấu hình Docker HostConfig theo chế độ lập lịch mong muốn.
func (m *CPUPolicyManager) BuildContainerConfig(mode CPUSchedulingMode, customWeight int) ContainerCPUConfig {
	if mode == CPUModeLegacyFixed {
		// Chế độ cũ gán cứng (chỉ dùng nếu backward compatibility yêu cầu)
		return ContainerCPUConfig{
			Mode:        CPUModeLegacyFixed,
			CpuShares:   1024,
			NanoCPUs:    4 * 1000000000,
			PidsLimit:   m.pidsLimit,
			CpusetCpus:  "",
			Description: "Legacy fixed core quota",
		}
	}

	weight := int64(m.defaultWeight)
	if customWeight > 0 {
		weight = int64(customWeight)
	}

	// SHARED_WEIGHTED: NanoCPUs = 0 (không giới hạn cố định số core, cho phép burst)
	// CpuShares = weight (chia tỷ lệ khi có tranh chấp)
	return ContainerCPUConfig{
		Mode:        CPUModeSharedWeighted,
		CpuShares:   weight,
		NanoCPUs:    0,
		PidsLimit:   m.pidsLimit,
		CpusetCpus:  "",
		Description: "Shared weighted CPU scheduling with fork bomb protection",
	}
}

// GetHostCores trả về số CPU logical cores của máy chủ.
func (m *CPUPolicyManager) GetHostCores() int {
	return runtime.NumCPU()
}
