package storage

import "time"

// QuotaBackend xác định cơ chế hạn mức dung lượng tầng filesystem.
type QuotaBackend string

const (
	QuotaBackendProjectQuota QuotaBackend = "PROJECT_QUOTA"
	QuotaBackendZFS          QuotaBackend = "ZFS_DATASET"
	QuotaBackendBtrfs        QuotaBackend = "BTRFS_QGROUP"
	QuotaBackendMockVerified QuotaBackend = "MOCK_VERIFIED"
	QuotaBackendNone         QuotaBackend = "NONE"
)

// VerificationStatus mô tả trạng thái xác minh thực tế trên runtime.
type VerificationStatus string

const (
	StatusVerified        VerificationStatus = "VERIFIED"
	StatusNotVerified     VerificationStatus = "NOT_VERIFIED"
	StatusIneligible      VerificationStatus = "INELIGIBLE"
	StatusQuotaNotEnforced VerificationStatus = "QUOTA_NOT_ENFORCEABLE"
)

// StoragePoolInfo chứa thông tin chi tiết của một ổ đĩa/volume đã xác minh trên host.
type StoragePoolInfo struct {
	HostID              string             `json:"host_id"`
	StorageID           string             `json:"storage_id"`
	Device              string             `json:"device"`
	FilesystemUUID      string             `json:"filesystem_uuid"`
	MountPoint          string             `json:"mount_point"`
	Filesystem          string             `json:"filesystem"`
	CapacityGiB         float64            `json:"capacity_gib"`
	UsedGiB             float64            `json:"used_gib"`
	FreeGiB             float64            `json:"free_gib"`
	AllocatableGiB      float64            `json:"allocatable_gib"`
	ReservedGiB         float64            `json:"reserved_gib"`
	QuotaBackend        QuotaBackend       `json:"quota_backend"`
	QuotaSupported      bool               `json:"quota_supported"`
	QuotaEnforced       bool               `json:"quota_enforced"`
	ReadOnly            bool               `json:"read_only"`
	Mounted             bool               `json:"mounted"`
	Eligible            bool               `json:"eligible"`
	IneligibilityReason string             `json:"ineligibility_reason,omitempty"`
	LastVerifiedAt      string             `json:"last_verified_at"`
	VerificationStatus  VerificationStatus `json:"verification_status"`
}

// MemberStorageRequest yêu cầu cấp phát thư mục lưu trữ riêng và áp đặt quota cho thành viên.
type MemberStorageRequest struct {
	MemberID           string `json:"member_id"`
	MemberUsername     string `json:"member_username"`
	HostID             string `json:"host_id"`
	StorageID          string `json:"storage_id"`
	StorageQuotaGiB    int64  `json:"storage_quota_gib"`
	ContainerMountPath string `json:"container_mount_path"` // Mặc định /workspace
	UID                int    `json:"uid"`
	GID                int    `json:"gid"`
	AuditActor         string `json:"audit_actor"`
	CorrelationID      string `json:"correlation_id"`
}

// MemberStorageResult kết quả sau khi tạo thư mục và xác minh quota thành công.
type MemberStorageResult struct {
	StorageID              string             `json:"storage_id"`
	HostStorageRoot        string             `json:"host_storage_root"`
	MemberStorageDirectory string             `json:"member_storage_directory"`
	ContainerMountPath     string             `json:"container_mount_path"`
	StorageQuotaGiB        int64              `json:"storage_quota_gib"`
	QuotaBackend           QuotaBackend       `json:"quota_backend"`
	QuotaEnforced          bool               `json:"quota_enforced"`
	VerificationStatus     VerificationStatus `json:"verification_status"`
	CreatedAt              string             `json:"created_at"`
}

// QuotaUsage lưu lượng sử dụng thực tế đọc từ filesystem quota.
type QuotaUsage struct {
	ProjectID      uint32  `json:"project_id"`
	Path           string  `json:"path"`
	UsedBytes      int64   `json:"used_bytes"`
	HardLimitBytes int64   `json:"hard_limit_bytes"`
	UsedGiB        float64 `json:"used_gib"`
	HardLimitGiB   int64   `json:"hard_limit_gib"`
	InodesUsed     uint64  `json:"inodes_used"`
	InodesLimit    uint64  `json:"inodes_limit"`
	IsExceeded     bool    `json:"is_exceeded"`
}

func nowISO() string {
	return time.Now().UTC().Format(time.RFC3339)
}
