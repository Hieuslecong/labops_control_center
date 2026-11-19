package resources

import "time"

// CPUSchedulingMode chế độ lập lịch CPU cho workspace.
type CPUSchedulingMode string

const (
	CPUModeSharedWeighted CPUSchedulingMode = "SHARED_WEIGHTED"
	CPUModeLegacyFixed    CPUSchedulingMode = "LEGACY_FIXED"
)

// MemoryProtectionMode chế độ bảo vệ RAM cấp host.
type MemoryProtectionMode string

const (
	MemoryModeSharedHostProtected MemoryProtectionMode = "SHARED_HOST_PROTECTED"
	MemoryModeLegacyFixed         MemoryProtectionMode = "LEGACY_FIXED"
)

// ResourceVerificationState trạng thái xác minh cơ chế bảo vệ tài nguyên trên kernel runtime.
type ResourceVerificationState string

const (
	PolicyVerified    ResourceVerificationState = "VERIFIED"
	PolicyNotVerified ResourceVerificationState = "NOT_VERIFIED"
	PolicyDegraded    ResourceVerificationState = "DEGRADED"
)

// HostResourceProfile cấu hình tài nguyên dùng chung và vùng bảo vệ của một máy chủ.
type HostResourceProfile struct {
	HostID             string                    `json:"host_id"`
	TotalCPUCores      int                       `json:"total_cpu_cores"`
	CPUSchedulingMode  CPUSchedulingMode         `json:"cpu_scheduling_mode"`
	DefaultCPUWeight   int                       `json:"default_cpu_weight"` // 100 cho cgroup v2, hoặc 1024 cho CpuShares
	TotalRAMBytes      int64                     `json:"total_ram_bytes"`
	TotalRAMGiB        float64                   `json:"total_ram_gib"`
	HostReserveBytes   int64                     `json:"host_reserve_bytes"`
	HostReserveGiB     float64                   `json:"host_reserve_gib"`
	SharedBudgetBytes  int64                     `json:"shared_budget_bytes"`
	SharedBudgetGiB    float64                   `json:"shared_budget_gib"`
	MemoryPolicy       MemoryProtectionMode      `json:"memory_policy"`
	PidsLimit          int64                     `json:"pids_limit"`
	CgroupV2Supported  bool                      `json:"cgroup_v2_supported"`
	PSIMonitoring      bool                      `json:"psi_monitoring"`
	VerificationStatus ResourceVerificationState `json:"verification_status"`
	LastEvaluatedAt    string                    `json:"last_evaluated_at"`
}

// MemoryPressureTelemetry số liệu áp lực bộ nhớ đọc từ Linux PSI (/proc/pressure/memory).
type MemoryPressureTelemetry struct {
	Avg10       float64 `json:"avg10"`
	Avg60       float64 `json:"avg60"`
	Avg300      float64 `json:"avg300"`
	TotalMicros uint64  `json:"total_micros"`
	Level       string  `json:"level"` // "NORMAL", "WARNING", "CRITICAL"
}

func nowISO() string {
	return time.Now().UTC().Format(time.RFC3339)
}
