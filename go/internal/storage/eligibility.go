package storage

import (
	"fmt"
	"strings"
)

// SystemForbiddenMountPoints danh sách mount points hệ thống tuyệt đối cấm làm nơi lưu dữ liệu người dùng.
var SystemForbiddenMountPoints = map[string]bool{
	"/":                true,
	"/boot":            true,
	"/boot/efi":        true,
	"/etc":             true,
	"/home":            true,
	"/root":            true,
	"/sys":             true,
	"/proc":            true,
	"/dev":             true,
	"/run":             true,
	"/tmp":             true,
	"/var":             true,
	"/var/tmp":         true,
	"/var/lib":         true,
	"/var/lib/docker":  true,
	"/var/lib/incus":   true,
	"/var/log":         true,
	"/usr":             true,
}

// SystemForbiddenPrefixes danh sách tiền tố đường dẫn hệ thống cấm cấp phát.
var SystemForbiddenPrefixes = []string{
	"/boot",
	"/etc",
	"/home",
	"/root",
	"/sys",
	"/proc",
	"/dev",
	"/run",
	"/usr",
	"/var",
}

// EvaluateEligibility đánh giá xem một mount point có đủ điều kiện lưu trữ workspace thành viên không.
func EvaluateEligibility(pool *StoragePoolInfo, requireHardQuota bool) (bool, string) {
	if pool == nil {
		return false, "Storage pool không tồn tại"
	}
	if !pool.Mounted {
		return false, "Ổ đĩa chưa được mount trên hệ điều hành"
	}
	if pool.ReadOnly {
		return false, "Filesystem đang ở chế độ chỉ đọc (read-only)"
	}

	cleanMount := strings.TrimRight(strings.TrimSpace(pool.MountPoint), "/")
	if cleanMount == "" {
		cleanMount = "/"
	}

	if SystemForbiddenMountPoints[cleanMount] {
		return false, fmt.Sprintf("Không thể cấp phát trên phân vùng hệ thống (%s)", cleanMount)
	}

	for _, prefix := range SystemForbiddenPrefixes {
		if cleanMount == prefix || strings.HasPrefix(cleanMount, prefix+"/") {
			return false, fmt.Sprintf("Đường dẫn nằm trong khu vực cấm của hệ thống (%s)", prefix)
		}
	}

	// Kiểm tra dung lượng khả dụng tối thiểu (ít nhất 10 GiB trống)
	if pool.FreeGiB < 10.0 {
		return false, fmt.Sprintf("Dung lượng trống không đủ (chỉ còn %.1f GiB)", pool.FreeGiB)
	}

	if requireHardQuota && !pool.QuotaSupported {
		return false, "Filesystem không hỗ trợ cơ chế hard quota (Project Quota / ZFS Quota)"
	}

	return true, ""
}
