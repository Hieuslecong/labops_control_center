package storage

import (
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

func TestStorageEligibility_Rules(t *testing.T) {
	// 1. Phân vùng root / phải bị từ chối
	rootPool := &StoragePoolInfo{
		MountPoint:     "/",
		Device:         "/dev/sda2",
		Mounted:        true,
		ReadOnly:       false,
		FreeGiB:        500,
		QuotaSupported: true,
	}
	eligible, reason := EvaluateEligibility(rootPool, false)
	if eligible {
		t.Errorf("Phân vùng root '/' PHẢI bị từ chối, nhưng lại được chấp thuận")
	}
	if !strings.Contains(reason, "hệ thống") {
		t.Errorf("Lý do từ chối không chính xác: %s", reason)
	}

	// 2. Phân vùng hệ thống /boot, /etc, /var/lib/docker
	sysPool := &StoragePoolInfo{
		MountPoint:     "/var/lib/docker",
		Device:         "/dev/sda2",
		Mounted:        true,
		ReadOnly:       false,
		FreeGiB:        200,
		QuotaSupported: true,
	}
	eligible, _ = EvaluateEligibility(sysPool, false)
	if eligible {
		t.Errorf("Phân vùng /var/lib/docker PHẢI bị từ chối")
	}

	// 3. Phân vùng read-only
	roPool := &StoragePoolInfo{
		MountPoint:     "/mnt/data-backup",
		Device:         "/dev/sdb1",
		Mounted:        true,
		ReadOnly:       true,
		FreeGiB:        1000,
		QuotaSupported: true,
	}
	eligible, _ = EvaluateEligibility(roPool, false)
	if eligible {
		t.Errorf("Phân vùng read-only PHẢI bị từ chối")
	}

	// 4. Phân vùng hợp lệ: /media/LabOps-Data
	validPool := &StoragePoolInfo{
		MountPoint:     "/media/LabOps-Data",
		Device:         "/dev/sdb1",
		Mounted:        true,
		ReadOnly:       false,
		FreeGiB:        500,
		QuotaSupported: true,
	}
	eligible, reason = EvaluateEligibility(validPool, false)
	if !eligible {
		t.Errorf("Phân vùng /media/LabOps-Data phải đủ điều kiện, nhưng bị từ chối: %s", reason)
	}
}

func TestStorageProvisioning_DedicatedDirectoryAndIsolation(t *testing.T) {
	tempStorageRoot := t.TempDir()
	qm := NewSystemQuotaManager(true)
	inv := NewInventoryCollector("workstation", qm)

	pool := &StoragePoolInfo{
		HostID:             "workstation",
		StorageID:          "sdb-data-verified-uuid",
		Device:             "/dev/sdb1",
		FilesystemUUID:     "verified-uuid-sdb",
		MountPoint:         tempStorageRoot,
		Filesystem:         "ext4",
		CapacityGiB:        800,
		UsedGiB:            100,
		FreeGiB:            700,
		AllocatableGiB:     620,
		ReservedGiB:        80,
		QuotaBackend:       QuotaBackendProjectQuota,
		QuotaSupported:     true,
		QuotaEnforced:      true,
		Mounted:            true,
		Eligible:           true,
		VerificationStatus: StatusVerified,
	}
	inv.RegisterCustomPool(pool)
	inv.RegisterApprovedRoot(tempStorageRoot)

	prov := NewProvisioner("workstation", inv, qm)

	// 1. Cấp phát cho User A (alice)
	resA, errA := prov.ProvisionMemberStorage(MemberStorageRequest{
		MemberID:           "mem-alice",
		MemberUsername:     "alice",
		HostID:             "workstation",
		StorageID:          pool.StorageID,
		StorageQuotaGiB:    100,
		ContainerMountPath: "/workspace",
		AuditActor:         "admin",
	})
	if errA != nil {
		t.Fatalf("Cấp phát cho alice thất bại: %v", errA)
	}

	// Kiểm tra đường dẫn thư mục chuẩn
	expectedDirA := filepath.Join(tempStorageRoot, "labops", "users", "alice")
	if resA.MemberStorageDirectory != expectedDirA {
		t.Errorf("Đường dẫn lưu trữ alice không đúng: nhận %s, kỳ vọng %s", resA.MemberStorageDirectory, expectedDirA)
	}

	// Kiểm tra các thư mục con
	for _, sub := range []string{"work", "checkpoints", "datasets", ".ssh"} {
		p := filepath.Join(expectedDirA, sub)
		if stat, err := os.Stat(p); err != nil || !stat.IsDir() {
			t.Errorf("Thư mục con %s chưa được tạo", sub)
		}
	}

	// 2. Cấp phát cho User B (bob) trên cùng một ổ đĩa
	resB, errB := prov.ProvisionMemberStorage(MemberStorageRequest{
		MemberID:           "mem-bob",
		MemberUsername:     "bob",
		HostID:             "workstation",
		StorageID:          pool.StorageID,
		StorageQuotaGiB:    150,
		ContainerMountPath: "/workspace",
		AuditActor:         "admin",
	})
	if errB != nil {
		t.Fatalf("Cấp phát cho bob thất bại: %v", errB)
	}

	expectedDirB := filepath.Join(tempStorageRoot, "labops", "users", "bob")
	if resB.MemberStorageDirectory != expectedDirB {
		t.Errorf("Đường dẫn lưu trữ bob không đúng: nhận %s, kỳ vọng %s", resB.MemberStorageDirectory, expectedDirB)
	}

	// Đảm bảo hai thư mục hoàn toàn tách biệt
	if resA.MemberStorageDirectory == resB.MemberStorageDirectory {
		t.Fatalf("LỖI BẢO MẬT: Thư mục của alice và bob bị trùng nhau!")
	}

	// 3. Kiểm tra Quota độc lập
	usageA, _ := qm.GetUsage(resA.MemberStorageDirectory, 10001)
	if usageA.HardLimitGiB != 100 {
		t.Errorf("Quota alice không đúng: %d GiB", usageA.HardLimitGiB)
	}
	usageB, _ := qm.GetUsage(resB.MemberStorageDirectory, 10002)
	if usageB.HardLimitGiB != 150 {
		t.Errorf("Quota bob không đúng: %d GiB", usageB.HardLimitGiB)
	}
}

func TestStorageProvisioning_SecuritySymlinkAndPathTraversal(t *testing.T) {
	tempStorageRoot := t.TempDir()
	qm := NewSystemQuotaManager(true)
	inv := NewInventoryCollector("workstation", qm)

	pool := &StoragePoolInfo{
		HostID:             "workstation",
		StorageID:          "sdb-uuid",
		MountPoint:         tempStorageRoot,
		FreeGiB:            500,
		AllocatableGiB:     400,
		QuotaSupported:     true,
		Mounted:            true,
		Eligible:           true,
		VerificationStatus: StatusVerified,
	}
	inv.RegisterCustomPool(pool)
	inv.RegisterApprovedRoot(tempStorageRoot)

	prov := NewProvisioner("workstation", inv, qm)

	// 1. Cố tình tấn công path traversal với username chứa ".."
	_, err := prov.ProvisionMemberStorage(MemberStorageRequest{
		MemberUsername:  "../../etc",
		StorageID:       pool.StorageID,
		StorageQuotaGiB: 50,
	})
	if err == nil {
		t.Errorf("LỖI BẢO MẬT: Path traversal qua username PHẢI bị từ chối")
	}

	// 2. Thử tấn công symlink nếu thư mục đích là symlink
	usersBase := filepath.Join(tempStorageRoot, "labops", "users")
	_ = os.MkdirAll(usersBase, 0700)
	evilTarget := filepath.Join(t.TempDir(), "evil-outside")
	_ = os.MkdirAll(evilTarget, 0700)
	symlinkPath := filepath.Join(usersBase, "attacker")
	_ = os.Symlink(evilTarget, symlinkPath)

	_, err = prov.ProvisionMemberStorage(MemberStorageRequest{
		MemberUsername:  "attacker",
		StorageID:       pool.StorageID,
		StorageQuotaGiB: 50,
	})
	if err == nil || !strings.Contains(err.Error(), "SECURITY_VIOLATION") {
		t.Errorf("LỖI BẢO MẬT: Cấp phát vào thư mục symlink PHẢI bị từ chối, nhận: %v", err)
	}
}

func TestStorageAdmission_CapacityAndConcurrency(t *testing.T) {
	tempStorageRoot := t.TempDir()
	qm := NewSystemQuotaManager(true)
	inv := NewInventoryCollector("workstation", qm)

	pool := &StoragePoolInfo{
		HostID:             "workstation",
		StorageID:          "small-pool-uuid",
		MountPoint:         tempStorageRoot,
		FreeGiB:            100,
		AllocatableGiB:     80, // Chỉ có 80 GiB khả dụng
		QuotaSupported:     true,
		Mounted:            true,
		Eligible:           true,
		VerificationStatus: StatusVerified,
	}
	inv.RegisterCustomPool(pool)
	inv.RegisterApprovedRoot(tempStorageRoot)

	prov := NewProvisioner("workstation", inv, qm)

	// 1. Cấp phát 50 GiB thành công
	_, err := prov.ProvisionMemberStorage(MemberStorageRequest{
		MemberUsername:  "user1",
		StorageID:       pool.StorageID,
		StorageQuotaGiB: 50,
	})
	if err != nil {
		t.Fatalf("Cấp phát 50 GiB thất bại: %v", err)
	}

	// 2. Cấp phát thêm 40 GiB (50 + 40 = 90 > 80 GiB allocatable) -> PHẢI BỊ TỪ CHỐI
	_, err = prov.ProvisionMemberStorage(MemberStorageRequest{
		MemberUsername:  "user2",
		StorageID:       pool.StorageID,
		StorageQuotaGiB: 40,
	})
	if err == nil {
		t.Fatalf("ADMISSION CONTROL LỖI: Vượt quá dung lượng khả dụng nhưng không bị từ chối")
	}

	// 3. Kiểm tra concurrent requests không gây race condition vượt quá giới hạn
	var wg sync.WaitGroup
	successCount := 0
	var mu sync.Mutex

	for i := 0; i < 5; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			_, provErr := prov.ProvisionMemberStorage(MemberStorageRequest{
				MemberUsername:  "concurrent_user",
				StorageID:       pool.StorageID,
				StorageQuotaGiB: 20,
			})
			if provErr == nil {
				mu.Lock()
				successCount++
				mu.Unlock()
			}
		}(i)
	}
	wg.Wait()

	// Chỉ có thể thêm tối đa 1 yêu cầu 20 GiB thành công (50 + 20 = 70 <= 80, cái thứ 2 sẽ là 90 > 80)
	if successCount > 1 {
		t.Errorf("RACE CONDITION: Có %d yêu cầu thành công, vượt quá allocatable capacity", successCount)
	}
}
