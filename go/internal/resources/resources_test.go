package resources

import (
	"testing"
)

func TestCPUPolicy_SharedWeighted(t *testing.T) {
	mgr := NewCPUPolicyManager(100, 4096)

	// 1. Cấu hình SHARED_WEIGHTED
	cfg := mgr.BuildContainerConfig(CPUModeSharedWeighted, 150)
	if cfg.Mode != CPUModeSharedWeighted {
		t.Errorf("Chế độ không đúng: %s", cfg.Mode)
	}
	if cfg.NanoCPUs != 0 {
		t.Errorf("Chế độ SHARED_WEIGHTED không được cố định NanoCPUs, nhận: %d", cfg.NanoCPUs)
	}
	if cfg.CpuShares != 150 {
		t.Errorf("CpuShares không đúng trọng số: %d", cfg.CpuShares)
	}
	if cfg.PidsLimit != 4096 {
		t.Errorf("PidsLimit cấm fork bomb phải là 4096, nhận: %d", cfg.PidsLimit)
	}

	// 2. Chế độ legacy fixed
	cfgLegacy := mgr.BuildContainerConfig(CPUModeLegacyFixed, 0)
	if cfgLegacy.NanoCPUs <= 0 {
		t.Errorf("Chế độ legacy phải có NanoCPUs cố định")
	}
}

func TestMemoryPolicy_SharedHostProtected(t *testing.T) {
	mgr := NewMemoryPolicyManager("workstation", 20.0)
	// Giả lập 64 GiB RAM
	total64GiB := int64(64) * 1024 * 1024 * 1024
	mgr.SetSimulatedRAM(total64GiB)

	profile := mgr.EvaluateHostProfile()
	if profile.TotalRAMGiB != 64.0 {
		t.Errorf("Tổng RAM không đúng: %.1f", profile.TotalRAMGiB)
	}

	// 20% của 64 GiB = 12.8 GiB
	if profile.HostReserveGiB < 12.0 || profile.HostReserveGiB > 13.0 {
		t.Errorf("Vùng dự phòng Host không chính xác: %.1f GiB", profile.HostReserveGiB)
	}

	// Shared Budget = 64 - 12.8 = 51.2 GiB
	if profile.SharedBudgetGiB < 51.0 || profile.SharedBudgetGiB > 52.0 {
		t.Errorf("Ngân sách dùng chung không chính xác: %.1f GiB", profile.SharedBudgetGiB)
	}

	// Cấu hình container
	cCfg := mgr.BuildContainerConfig(MemoryModeSharedHostProtected)
	if cCfg.MemoryLimitBytes != profile.SharedBudgetBytes {
		t.Errorf("Trần RAM container phải khớp với SharedBudgetBytes để bảo vệ host: %d vs %d",
			cCfg.MemoryLimitBytes, profile.SharedBudgetBytes)
	}
	if cCfg.MemorySwapBytes != cCfg.MemoryLimitBytes {
		t.Errorf("MemorySwapBytes phải bằng MemoryLimitBytes để tránh swap thrashing trên host")
	}
}
