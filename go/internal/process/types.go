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

// ProcessInfo lưu thông tin an toàn về một tiến trình đang chạy
type ProcessInfo struct {
	Ref               string        `json:"ref"`                       // Định danh an toàn server:container:pid:starttime
	PID               int           `json:"pid"`
	PPID              int           `json:"ppid"`
	PGID              int           `json:"pgid"`
	UID               int           `json:"uid"`
	User              string        `json:"user"`
	Name              string        `json:"name"`
	CommandLine       string        `json:"command_line,omitempty"`    // Rút gọn và lọc dữ liệu nhạy cảm
	Classification    string        `json:"classification"`            // SYSTEM_PROCESS | MEMBER_PROCESS | RESEARCH_JOB | GPU_PROCESS
	Attribution       string        `json:"attribution"`               // VERIFIED_USER_WORKLOAD | LIKELY_USER_WORKLOAD | BACKGROUND_PROCESS | SYSTEM_PROCESS | UNKNOWN
	IsUserWorkload    bool          `json:"is_user_workload"`          // True nếu là tác vụ thành viên chủ động chạy
	IsRootWorkload    bool          `json:"is_root_workload"`          // True nếu là tiến trình gốc của nhóm workload
	ParentWorkloadPID int           `json:"parent_workload_pid,omitempty"` // PID gốc nếu là worker con
	ChildrenCount     int           `json:"children_count"`            // Số lượng tiến trình con trong workload
	TotalCPUPercent   float64       `json:"total_cpu_percent"`         // Tổng CPU cả cây tiến trình (không double-count)
	TotalMemoryRSSMB  float64       `json:"total_memory_rss_mb"`       // Tổng RAM RSS cả cây tiến trình
	ContainerID       string        `json:"container_id,omitempty"`    // ID container ngắn
	Workspace         string        `json:"workspace,omitempty"`       // Tên workspace (e.g. ws-testacc)
	CPUPercent        float64       `json:"cpu_percent"`
	MemoryRSSMB       float64       `json:"memory_rss_mb"`
	MemoryVMSMB       float64       `json:"memory_vms_mb"`
	MemoryPercent     float64       `json:"memory_percent"`
	State             string        `json:"state"`
	StartTime         string        `json:"start_time"`                // "YYYY-MM-DD HH:MM:SS"
	StartTimeTicks    uint64        `json:"start_time_ticks"`          // Kernel starttime ticks chống PID reuse
	ElapsedTime       string        `json:"elapsed_time"`              // "01h 20m 10s"
	IsGPU             bool          `json:"is_gpu"`
	GPUIndex          *int          `json:"gpu_index,omitempty"`
	GPUUUID           string        `json:"gpu_uuid,omitempty"`
	VRAMMB            uint64        `json:"vram_mb,omitempty"`
	IsProtected       bool          `json:"is_protected"`              // True nếu thuộc whitelist không được kill
	Children          []ProcessInfo `json:"children,omitempty"`        // Danh sách tiến trình con khi query tree
}

// ProcessReport báo cáo tổng quan tiến trình
type ProcessReport struct {
	Source      string        `json:"source"`
	CollectedAt string        `json:"collected_at"`
	ServerID    string        `json:"server_id"`
	TotalCount  int           `json:"total_count"`
	Processes   []ProcessInfo `json:"processes"`
}

// ControlResult kết quả thực thi tín hiệu tiến trình
type ControlResult struct {
	Status      string    `json:"status"`       // TERMINATED | KILLED | TIMEOUT_STILL_RUNNING
	Action      string    `json:"action"`       // SIGTERM | SIGKILL
	Ref         string    `json:"ref"`
	PID         int       `json:"pid"`
	Message     string    `json:"message"`
	ExecutedAt  time.Time `json:"executed_at"`
	PIDsKilled  []int     `json:"pids_killed,omitempty"`
}
