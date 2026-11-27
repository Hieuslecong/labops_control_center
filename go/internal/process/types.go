package process

import "time"

// Classification constants for processes
const (
	ClassSystemProcess = "SYSTEM_PROCESS"
	ClassMemberProcess = "MEMBER_PROCESS"
	ClassResearchJob   = "RESEARCH_JOB"
	ClassGPUProcess    = "GPU_PROCESS"
)

// Attribution constants theo chuẩn LabOps Process Classification
const (
	AttributionVerifiedWorkload  = "VERIFIED_USER_WORKLOAD"  // Tác vụ người dùng có chứng cứ xác thực (CUDA, script, ML/AI, binary)
	AttributionLikelyWorkload    = "LIKELY_USER_WORKLOAD"    // Tác vụ nghi vấn người dùng chủ động chạy
	AttributionBackgroundProcess = "BACKGROUND_PROCESS"      // Tiến trình nền (IDE helper, desktop session, idle shell)
	AttributionSystemProcess     = "SYSTEM_PROCESS"          // Hệ điều hành và nền tảng hạ tầng
	AttributionUnknown           = "UNKNOWN"
)

// ProcessGPUAllocation lưu phân bổ VRAM theo từng cặp (PID, GPU UUID).
type ProcessGPUAllocation struct {
	GPUUUID    string `json:"gpu_uuid"`
	GPUIndex   int    `json:"gpu_index"`
	ValueBytes uint64 `json:"value_bytes"`
	VRAMMB     uint64 `json:"vram_mb"`
}

// ProcessInfo lưu thông tin an toàn về một tiến trình đang chạy
type ProcessInfo struct {
	Ref               string                 `json:"ref"`                       // Định danh an toàn server:container:pid:starttime
	PID               int                    `json:"pid"`
	PPID              int                    `json:"ppid"`
	PGID              int                    `json:"pgid"`
	UID               int                    `json:"uid"`
	User              string                 `json:"user"`
	Name              string                 `json:"name"`
	CommandLine       string                 `json:"command_line,omitempty"`    // Rút gọn và lọc dữ liệu nhạy cảm
	Classification    string                 `json:"classification"`            // SYSTEM_PROCESS | MEMBER_PROCESS | RESEARCH_JOB | GPU_PROCESS
	Attribution       string                 `json:"attribution"`               // VERIFIED_USER_WORKLOAD | LIKELY_USER_WORKLOAD | BACKGROUND_PROCESS | SYSTEM_PROCESS | UNKNOWN
	WorkloadKind      string                 `json:"workload_kind,omitempty"`   // TRAINING | INFERENCE | NOTEBOOK | GENERAL_COMPUTE | MONITORING | SYSTEM | OTHER | UNKNOWN
	KindConfidence    string                 `json:"kind_confidence,omitempty"` // VERIFIED | HEURISTIC | UNKNOWN
	KindEvidence      []string               `json:"kind_evidence,omitempty"`
	IsUserWorkload    bool                   `json:"is_user_workload"`          // True nếu là tác vụ thành viên chủ động chạy
	IsRootWorkload    bool                   `json:"is_root_workload"`          // True nếu là tiến trình gốc của nhóm workload
	ParentWorkloadPID int                    `json:"parent_workload_pid,omitempty"` // PID gốc nếu là worker con
	ChildrenCount     int                    `json:"children_count"`            // Số lượng tiến trình con trong workload
	TotalCPUPercent   float64                `json:"total_cpu_percent"`         // Tổng CPU cả cây tiến trình (không double-count)
	TotalMemoryRSSMB  float64                `json:"total_memory_rss_mb"`       // Tổng RAM RSS cả cây tiến trình
	ContainerID       string                 `json:"container_id,omitempty"`    // ID container ngắn
	Workspace         string                 `json:"workspace,omitempty"`       // Tên workspace (e.g. ws-testacc)
	BootID            string                 `json:"boot_id,omitempty"`         // Kernel /proc/sys/kernel/random/boot_id
	CPUPercent        float64                `json:"cpu_percent"`
	MemoryRSSMB       float64                `json:"memory_rss_mb"`
	MemoryVMSMB       float64                `json:"memory_vms_mb"`
	MemoryPercent     float64                `json:"memory_percent"`
	State             string                 `json:"state"`
	StartTime         string                 `json:"start_time"`                // "YYYY-MM-DD HH:MM:SS"
	StartedAtUTC      string                 `json:"started_at_utc,omitempty"`  // RFC3339 UTC
	StartTimeTicks    uint64                 `json:"start_time_ticks"`          // Kernel starttime ticks chống PID reuse
	ElapsedTime       string                 `json:"elapsed_time"`              // "01h 20m 10s"
	ElapsedSeconds    int64                  `json:"elapsed_seconds"`           // Numeric runtime seconds
	IsGPU             bool                   `json:"is_gpu"`
	GPUIndex          *int                   `json:"gpu_index,omitempty"`
	GPUUUID           string                 `json:"gpu_uuid,omitempty"`
	VRAMMB            uint64                 `json:"vram_mb,omitempty"`
	TotalVRAMMB       uint64                 `json:"total_vram_mb,omitempty"`     // Tổng VRAM cả cây tiến trình
	TreeIsGPU         bool                   `json:"tree_is_gpu,omitempty"`       // True nếu tiến trình gốc hoặc bất kỳ tiến trình con nào đang dùng GPU
	TreeGPUIndex      *int                   `json:"tree_gpu_index,omitempty"`    // GPU index nhỏ nhất/đại diện của cây tiến trình
	TreeGPUUUID       string                 `json:"tree_gpu_uuid,omitempty"`     // GPU UUID đại diện của cây tiến trình
	GPUAllocations    []ProcessGPUAllocation `json:"gpu_allocations,omitempty"`   // Phân bổ theo từng GPU UUID (không double-count)
	IsProtected       bool                   `json:"is_protected"`              // True nếu thuộc whitelist không được kill
	Children          []ProcessInfo          `json:"children,omitempty"`        // Danh sách tiến trình con khi query tree
}

// ProcessReport báo cáo tổng quan tiến trình
type ProcessReport struct {
	Source      string        `json:"source"`
	CollectedAt string        `json:"collected_at"`
	ServerID    string        `json:"server_id"`
	BootID      string        `json:"boot_id,omitempty"`
	TotalCount  int           `json:"total_count"`
	Processes   []ProcessInfo `json:"processes"`
}

// ControlResult kết quả thực thi tín hiệu tiến trình
type ControlResult struct {
	Status             string         `json:"status"` // TERMINATED | KILLED | TIMEOUT_STILL_RUNNING | FAILED_STILL_RUNNING | PARTIAL_FAILURE
	Action             string         `json:"action"` // SIGTERM | SIGKILL
	Verification       string         `json:"verification"` // VERIFIED | PARTIAL_FAILURE | STILL_RUNNING | FAILED
	Ref                string         `json:"ref"`
	PID                int            `json:"pid"`
	Message            string         `json:"message"`
	ExecutedAt         time.Time      `json:"executed_at"`
	PIDsSignaled       []int          `json:"pids_signaled,omitempty"`
	PIDsKilled         []int          `json:"pids_killed,omitempty"`
	PIDsRemaining      []int          `json:"pids_remaining,omitempty"`
	SkippedDescendants []string       `json:"skipped_descendants,omitempty"`
	SignalErrors       map[int]string `json:"signal_errors,omitempty"`
}
