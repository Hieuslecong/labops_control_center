package storage

import (
	"crypto/sha256"
	"encoding/binary"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

// Provisioner điều phối việc tạo thư mục, bảo vệ chống breakout/symlink và áp đặt quota.
type Provisioner struct {
	nodeID        string
	inventory     *InventoryCollector
	quotaMgr      QuotaManager
	mu            sync.Mutex
	reservations  map[string]int64 // StorageID -> Tổng quota đã cam kết (GiB)
}

// NewProvisioner khởi tạo bộ cấp phát thư mục và quota.
func NewProvisioner(nodeID string, inv *InventoryCollector, qm QuotaManager) *Provisioner {
	if qm == nil {
		qm = NewSystemQuotaManager(false)
	}
	return &Provisioner{
		nodeID:       nodeID,
		inventory:    inv,
		quotaMgr:     qm,
		reservations: make(map[string]int64),
	}
}

// ValidateAllocation kiểm tra dung lượng và tính đủ điều kiện trước khi cấp phát.
func (p *Provisioner) ValidateAllocation(storageID string, quotaGiB int64) (*StoragePoolInfo, error) {
	p.mu.Lock()
	defer p.mu.Unlock()

	pool := p.inventory.GetStoragePoolByID(storageID)
	if pool == nil {
		return nil, fmt.Errorf("không tìm thấy storage pool với ID '%s' trên node '%s'", storageID, p.nodeID)
	}

	eligible, reason := EvaluateEligibility(pool, false)
	if !eligible {
		return nil, fmt.Errorf("storage pool '%s' không đủ điều kiện cấp phát: %s", storageID, reason)
	}

	committed := p.reservations[storageID]
	availableAfterCommitted := pool.AllocatableGiB - float64(committed)

	if float64(quotaGiB) > availableAfterCommitted {
		return nil, fmt.Errorf("dung lượng khả dụng không đủ: yêu cầu %d GiB, nhưng chỉ còn %.1f GiB có thể cấp phát (đã cam kết: %d GiB)",
			quotaGiB, availableAfterCommitted, committed)
	}

	return pool, nil
}

// ReserveCapacity tạm giữ dung lượng trước khi thực hiện các tác vụ tốn thời gian.
func (p *Provisioner) ReserveCapacity(storageID string, quotaGiB int64) error {
	p.mu.Lock()
	defer p.mu.Unlock()

	pool := p.inventory.GetStoragePoolByID(storageID)
	if pool == nil {
		return fmt.Errorf("storage pool '%s' không tồn tại", storageID)
	}

	committed := p.reservations[storageID]
	if float64(quotaGiB) > (pool.AllocatableGiB - float64(committed)) {
		return fmt.Errorf("không thể giữ chỗ: vượt quá dung lượng khả dụng")
	}

	p.reservations[storageID] = committed + quotaGiB
	return nil
}

// ReleaseReservation giải phóng dung lượng đã giữ chỗ nếu tác vụ thất bại.
func (p *Provisioner) ReleaseReservation(storageID string, quotaGiB int64) {
	p.mu.Lock()
	defer p.mu.Unlock()

	current := p.reservations[storageID]
	if current <= quotaGiB {
		delete(p.reservations, storageID)
	} else {
		p.reservations[storageID] = current - quotaGiB
	}
}

// ProvisionMemberStorage tạo thư mục riêng cho thành viên trên ổ đĩa đã chọn và áp đặt quota.
func (p *Provisioner) ProvisionMemberStorage(req MemberStorageRequest) (*MemberStorageResult, error) {
	username := strings.ToLower(strings.TrimSpace(req.MemberUsername))
	if username == "" || strings.Contains(username, "..") || strings.Contains(username, "/") || strings.Contains(username, "\\") {
		return nil, fmt.Errorf("tên người dùng không hợp lệ: '%s'", req.MemberUsername)
	}

	// 1. Kiểm tra admission & storage pool
	pool, err := p.ValidateAllocation(req.StorageID, req.StorageQuotaGiB)
	if err != nil {
		return nil, fmt.Errorf("ADMISSION_REJECTED: %w", err)
	}

	// 2. Tạm giữ chỗ dung lượng
	if rErr := p.ReserveCapacity(req.StorageID, req.StorageQuotaGiB); rErr != nil {
		return nil, rErr
	}

	// 3. Xây dựng đường dẫn tuyệt đối theo chuẩn: <selected-storage-root>/labops/users/<username>
	storageRoot := filepath.Clean(pool.MountPoint)
	expectedBaseDir := filepath.Join(storageRoot, "labops", "users")
	memberDir := filepath.Join(expectedBaseDir, username)

	// Bảo vệ chống symlink / path traversal attack: Đường dẫn phải nằm chặt bên trong expectedBaseDir
	cleanMemberDir := filepath.Clean(memberDir)
	if !strings.HasPrefix(cleanMemberDir, expectedBaseDir+string(filepath.Separator)) {
		p.ReleaseReservation(req.StorageID, req.StorageQuotaGiB)
		return nil, fmt.Errorf("SECURITY_VIOLATION: Phát hiện nguy cơ thoát khỏi storage root (Path traversal): %s", memberDir)
	}

	// 4. Kiểm tra xem thư mục đã tồn tại chưa (Idempotency & Collision check)
	if stat, statErr := os.Lstat(cleanMemberDir); statErr == nil {
		// Nếu là symlink: Cực kỳ nguy hiểm -> Từ chối ngay
		if stat.Mode()&os.ModeSymlink != 0 {
			p.ReleaseReservation(req.StorageID, req.StorageQuotaGiB)
			return nil, fmt.Errorf("SECURITY_VIOLATION: Thư mục mục tiêu là một symlink, từ chối cấp phát")
		}
	}

	// 5. Tạo thư mục với quyền 0700
	if mkErr := os.MkdirAll(cleanMemberDir, 0700); mkErr != nil {
		p.ReleaseReservation(req.StorageID, req.StorageQuotaGiB)
		return nil, fmt.Errorf("không thể tạo thư mục lưu trữ '%s': %w", cleanMemberDir, mkErr)
	}

	// Gán quyền 0700 chặt chẽ
	_ = os.Chmod(cleanMemberDir, 0700)

	// Tạo các thư mục con phục vụ nghiên cứu AI
	subdirs := []string{"work", "checkpoints", "datasets", ".ssh"}
	for _, sub := range subdirs {
		subPath := filepath.Join(cleanMemberDir, sub)
		if subErr := os.MkdirAll(subPath, 0700); subErr == nil {
			_ = os.Chmod(subPath, 0700)
		}
	}

	// 6. Gán UID/GID nếu được chỉ định
	if req.UID > 0 && req.GID > 0 {
		_ = os.Chown(cleanMemberDir, req.UID, req.GID)
		for _, sub := range subdirs {
			_ = os.Chown(filepath.Join(cleanMemberDir, sub), req.UID, req.GID)
		}
	}

	// 7. Sinh Project ID tất định từ username (32-bit uint)
	h := sha256.Sum256([]byte(username))
	projectID := binary.BigEndian.Uint32(h[:4])
	if projectID == 0 {
		projectID = 10001
	}

	// 8. Áp đặt Filesystem Quota
	quotaEnforced := false
	quotaBackend := pool.QuotaBackend
	if setErr := p.quotaMgr.SetDirectoryQuota(cleanMemberDir, projectID, req.StorageQuotaGiB); setErr != nil {
		// Ghi nhận lỗi nhưng không xóa dữ liệu người dùng
		quotaEnforced = false
	} else {
		// Xác minh quota read-back
		verified, _ := p.quotaMgr.VerifyEnforcement(cleanMemberDir, req.StorageQuotaGiB)
		quotaEnforced = verified
	}

	vStatus := StatusVerified
	if !quotaEnforced {
		vStatus = StatusQuotaNotEnforced
	}

	containerMount := req.ContainerMountPath
	if containerMount == "" {
		containerMount = "/workspace"
	}

	return &MemberStorageResult{
		StorageID:              pool.StorageID,
		HostStorageRoot:        storageRoot,
		MemberStorageDirectory: cleanMemberDir,
		ContainerMountPath:     containerMount,
		StorageQuotaGiB:        req.StorageQuotaGiB,
		QuotaBackend:           quotaBackend,
		QuotaEnforced:          quotaEnforced,
		VerificationStatus:     vStatus,
		CreatedAt:              nowISO(),
	}, nil
}
