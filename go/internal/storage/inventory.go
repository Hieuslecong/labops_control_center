package storage

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"math"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
)

// InventoryCollector thu thập và theo dõi danh sách ổ đĩa/volume thực tế trên host.
type InventoryCollector struct {
	nodeID        string
	quotaMgr      QuotaManager
	mu            sync.RWMutex
	approvedRoots []string
	customPools   map[string]*StoragePoolInfo
	mountsPath    string
	statfsFn      func(string, *syscall.Statfs_t) error
	uuidFn        func(device, mountPoint string) string
}

type mountEntry struct {
	device     string
	mountPoint string
	fsType     string
	opts       []string
	isApproved bool
}

// NewInventoryCollector khởi tạo bộ thu thập inventory.
func NewInventoryCollector(nodeID string, quotaMgr QuotaManager) *InventoryCollector {
	if quotaMgr == nil {
		quotaMgr = NewSystemQuotaManager(false)
	}
	c := &InventoryCollector{
		nodeID:      nodeID,
		quotaMgr:    quotaMgr,
		customPools: make(map[string]*StoragePoolInfo),
		mountsPath:  "/proc/mounts",
		statfsFn:    syscall.Statfs,
		uuidFn:      getFilesystemUUID,
		approvedRoots: []string{
			"/mnt/HDD3",
			"/media/LabOps-Data",
			"/tank/workspaces",
			"/mnt/labops-data",
			"/data",
		},
	}
	return c
}

// RegisterApprovedRoot thêm một đường dẫn thư mục lưu trữ được Admin phê duyệt.
func (c *InventoryCollector) RegisterApprovedRoot(rootPath string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	clean := filepath.Clean(rootPath)
	for _, r := range c.approvedRoots {
		if r == clean {
			return
		}
	}
	c.approvedRoots = append(c.approvedRoots, clean)
}

// RegisterCustomPool cho phép đăng ký mock / test storage pool trong môi trường kiểm thử cô lập.
func (c *InventoryCollector) RegisterCustomPool(pool *StoragePoolInfo) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if pool != nil && pool.StorageID != "" {
		c.customPools[pool.StorageID] = pool
	}
}

// CollectStoragePools quét và trả về danh sách đầy đủ các ổ đĩa/volume trên host.
func (c *InventoryCollector) CollectStoragePools() []*StoragePoolInfo {
	c.mu.RLock()
	defer c.mu.RUnlock()

	var result []*StoragePoolInfo
	customUUIDs := make(map[string]bool)
	customMounts := make(map[string]bool)

	// 1. Thêm các custom / registered pools trước (dùng cho test/staging)
	for _, p := range c.customPools {
		cpy := *p
		cpy.HostID = c.nodeID
		cpy.LastVerifiedAt = nowISO()
		result = append(result, &cpy)
		if cpy.FilesystemUUID != "" {
			customUUIDs[cpy.FilesystemUUID] = true
		}
		customMounts[cpy.MountPoint] = true
	}

	mountsPath := c.mountsPath
	if mountsPath == "" {
		mountsPath = "/proc/mounts"
	}
	statfsFn := c.statfsFn
	if statfsFn == nil {
		statfsFn = syscall.Statfs
	}
	uuidFn := c.uuidFn
	if uuidFn == nil {
		uuidFn = getFilesystemUUID
	}

	// 2. Quét /proc/mounts theo 2 pha để xử lý đúng stacked mounts và chọn mount ưu tiên khi trùng UUID
	if file, err := os.Open(mountsPath); err == nil {
		defer file.Close()
		scanner := bufio.NewScanner(file)

		effectiveByMount := make(map[string]mountEntry)
		var mountOrder []string

		for scanner.Scan() {
			fields := strings.Fields(scanner.Text())
			if len(fields) < 4 {
				continue
			}
			device := fields[0]
			mountPoint := fields[1]
			fsType := fields[2]
			opts := strings.Split(fields[3], ",")

			// Bỏ qua loop devices, các filesystem ảo/boot và per-user sub-mounts
			if strings.HasPrefix(device, "/dev/loop") || strings.HasPrefix(device, "none") ||
				fsType == "cgroup" || fsType == "cgroup2" ||
				fsType == "sysfs" || fsType == "proc" || fsType == "devpts" || fsType == "hugetlbfs" ||
				fsType == "mqueue" || fsType == "securityfs" || fsType == "pstore" || fsType == "bpf" ||
				fsType == "autofs" || fsType == "fusectl" || fsType == "vfat" || fsType == "squashfs" ||
				fsType == "iso9660" || fsType == "devtmpfs" || fsType == "tmpfs" || fsType == "overlay" ||
				fsType == "efivarfs" || fsType == "tracefs" || fsType == "debugfs" || fsType == "configfs" ||
				fsType == "binfmt_misc" || fsType == "nsfs" || strings.HasPrefix(fsType, "fuse") ||
				strings.Contains(mountPoint, "/users/") || strings.Contains(mountPoint, "/.quota-images") {
				continue
			}

			if customMounts[mountPoint] {
				continue
			}

			// Chỉ xem xét các thiết bị block thực (/dev/...) hoặc các approved roots
			isBlockDev := strings.HasPrefix(device, "/dev/")
			isApproved := false
			for _, app := range c.approvedRoots {
				if mountPoint == app || strings.HasPrefix(mountPoint, app+"/") {
					isApproved = true
					break
				}
			}

			if !isBlockDev && !isApproved {
				continue
			}

			if _, seen := effectiveByMount[mountPoint]; !seen {
				mountOrder = append(mountOrder, mountPoint)
			}
			// Dòng xuất hiện sau trên cùng mountPoint sẽ ghi đè dòng trước (stacked mount)
			effectiveByMount[mountPoint] = mountEntry{
				device:     device,
				mountPoint: mountPoint,
				fsType:     fsType,
				opts:       opts,
				isApproved: isApproved,
			}
		}

		uuidToIdx := make(map[string]int)
		uuidApproved := make(map[string]bool)

		for _, mountPoint := range mountOrder {
			entry := effectiveByMount[mountPoint]

			var stat syscall.Statfs_t
			if errStat := statfsFn(entry.mountPoint, &stat); errStat != nil {
				continue
			}

			totalBytes := int64(stat.Blocks) * int64(stat.Bsize)
			freeBytes := int64(stat.Bavail) * int64(stat.Bsize)
			if totalBytes <= 0 {
				continue
			}

			capacityGiB := math.Round(float64(totalBytes)/(1024*1024*1024)*10) / 10
			freeGiB := math.Round(float64(freeBytes)/(1024*1024*1024)*10) / 10
			usedGiB := math.Round((capacityGiB-freeGiB)*10) / 10

			isReadOnly := false
			for _, o := range entry.opts {
				if o == "ro" {
					isReadOnly = true
					break
				}
			}

			// Tạo stable Storage ID bằng hash mountPoint + device hoặc filesystem UUID
			fsUUID := uuidFn(entry.device, entry.mountPoint)
			if fsUUID != "" && customUUIDs[fsUUID] {
				continue
			}

			storageID := fsUUID
			if storageID == "" {
				hasher := sha256.New()
				hasher.Write([]byte(c.nodeID + ":" + entry.mountPoint + ":" + entry.device))
				storageID = "fs-" + hex.EncodeToString(hasher.Sum(nil))[:12]
			}

			// Tính allocatable: Trừ 10% safety reserve (tối thiểu 10 GiB)
			safetyReserve := math.Max(10.0, capacityGiB*0.1)
			allocatableGiB := math.Max(0.0, math.Round((freeGiB-safetyReserve)*10)/10)

			quotaSupported, quotaBackend := c.quotaMgr.CheckSupport(entry.mountPoint, entry.fsType)

			pool := &StoragePoolInfo{
				HostID:             c.nodeID,
				StorageID:          storageID,
				Device:             entry.device,
				FilesystemUUID:     fsUUID,
				MountPoint:         entry.mountPoint,
				Filesystem:         entry.fsType,
				CapacityGiB:        capacityGiB,
				UsedGiB:            usedGiB,
				FreeGiB:            freeGiB,
				AllocatableGiB:     allocatableGiB,
				ReservedGiB:        safetyReserve,
				QuotaBackend:       quotaBackend,
				QuotaSupported:     quotaSupported,
				QuotaEnforced:      false,
				ReadOnly:           isReadOnly,
				Mounted:            true,
				LastVerifiedAt:     nowISO(),
				VerificationStatus: StatusVerified,
			}

			eligible, reason := EvaluateEligibility(pool, false)
			pool.Eligible = eligible
			pool.IneligibilityReason = reason
			if !eligible {
				pool.VerificationStatus = StatusIneligible
			} else if !quotaSupported {
				pool.VerificationStatus = StatusQuotaNotEnforced
			}

			if fsUUID != "" {
				if existingIdx, exists := uuidToIdx[fsUUID]; exists {
					if shouldPreferPool(pool, result[existingIdx], entry.isApproved, uuidApproved[fsUUID]) {
						result[existingIdx] = pool
						uuidApproved[fsUUID] = entry.isApproved
					}
					continue
				}
				uuidToIdx[fsUUID] = len(result)
				uuidApproved[fsUUID] = entry.isApproved
			}

			result = append(result, pool)
		}
	}

	return result
}

func shouldPreferPool(candidate, existing *StoragePoolInfo, candidateApproved, existingApproved bool) bool {
	if existing.MountPoint == "/" {
		return false
	}
	if candidate.MountPoint == "/" {
		return true
	}
	if candidate.Eligible != existing.Eligible {
		return candidate.Eligible
	}
	if candidate.ReadOnly != existing.ReadOnly {
		return !candidate.ReadOnly
	}
	if candidateApproved != existingApproved {
		return candidateApproved
	}
	return false
}

// GetStoragePoolByID tìm một pool cụ thể bằng ID bất biến.
func (c *InventoryCollector) GetStoragePoolByID(storageID string) *StoragePoolInfo {
	pools := c.CollectStoragePools()
	for _, p := range pools {
		if p.StorageID == storageID || p.FilesystemUUID == storageID {
			return p
		}
	}
	return nil
}

// getFilesystemUUID tìm UUID của phân vùng từ /dev/disk/by-uuid
func getFilesystemUUID(device, mountPoint string) string {
	devBase := filepath.Base(device)
	uuidDir := "/dev/disk/by-uuid"
	if entries, err := os.ReadDir(uuidDir); err == nil {
		for _, e := range entries {
			target, errLink := os.Readlink(filepath.Join(uuidDir, e.Name()))
			if errLink == nil && filepath.Base(target) == devBase {
				return e.Name()
			}
		}
	}

	// Tạo ID ổn định dự phòng từ đường dẫn
	hasher := sha256.New()
	hasher.Write([]byte(mountPoint + ":" + device))
	return "uuid-" + hex.EncodeToString(hasher.Sum(nil))[:16]
}
