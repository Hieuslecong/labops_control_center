package process

import (
	"os"
	"testing"
)

func TestProcessCollector_CollectCurrentProcess(t *testing.T) {
	collector := NewCollector("track", nil)
	report := collector.Collect("all")

	if report == nil {
		t.Fatal("Collect('all') trả về nil")
	}

	if report.TotalCount <= 0 {
		t.Errorf("Kỳ vọng TotalCount > 0, nhận được: %d", report.TotalCount)
	}

	myPID := os.Getpid()
	foundMyPID := false
	for _, p := range report.Processes {
		if p.PID == myPID {
			foundMyPID = true
			if p.User == "" {
				t.Errorf("Tiến trình hiện tại thiếu thông tin người dùng")
			}
			break
		}
	}

	if !foundMyPID {
		t.Errorf("Không tìm thấy chính tiến trình test (PID %d) trong kết quả quét /proc", myPID)
	}
}

func TestProcessClassification_Rules(t *testing.T) {
	// 1. Idle shell phải bị loại khỏi workload
	if !IsIdleShell("sh", "sh") {
		t.Errorf("Kỳ vọng 'sh' là idle shell")
	}
	if !IsIdleShell("bash", "/bin/bash --init-file shellIntegration-bash.sh") {
		t.Errorf("Kỳ vọng bash với shellIntegration là idle shell")
	}
	if IsIdleShell("bash", "bash train.sh") {
		t.Errorf("Kỳ vọng 'bash train.sh' KHÔNG phải là idle shell")
	}

	// 2. Desktop & IDE helpers phải bị nhận diện là background service
	if !IsDesktopOrBackgroundService("dbus-daemon", "/usr/bin/dbus-daemon --session") {
		t.Errorf("Kỳ vọng dbus-daemon là background service")
	}
	if !IsDesktopOrBackgroundService("pulseaudio", "/usr/bin/pulseaudio") {
		t.Errorf("Kỳ vọng pulseaudio là background service")
	}
	if !IsDesktopOrBackgroundService("node", "node /home/user/.vscode-server/server/out/bootstrap-fork --type=fileWatcher") {
		t.Errorf("Kỳ vọng VS Code fileWatcher là background service")
	}
	if !IsDesktopOrBackgroundService("copilot-runtime", "/path/copilot-runtime --headless") {
		t.Errorf("Kỳ vọng copilot-runtime là background service")
	}

	// 3. Workload candidates
	// Python backend của LabOps -> ẩn, không phải user workload
	isBackend, _ := IsWorkloadCandidate("python3", "./backend/.venv/bin/python backend/app/main.py", false, 0)
	if isBackend {
		t.Errorf("Kỳ vọng Python backend của LabOps KHÔNG phải là user workload")
	}

	// Python train.py
	isWorkload, attr := IsWorkloadCandidate("python3", "python3 train.py --epochs 10", false, 0)
	if !isWorkload || attr != AttributionVerifiedWorkload {
		t.Errorf("Kỳ vọng 'python3 train.py' là Verified Workload, nhận được: %v, %s", isWorkload, attr)
	}

	// Python script không có từ khóa train
	isWorkload, attr = IsWorkloadCandidate("python", "python extract_features.py", false, 0)
	if !isWorkload || attr != AttributionVerifiedWorkload {
		t.Errorf("Kỳ vọng 'python extract_features.py' là Verified Workload, nhận được: %v, %s", isWorkload, attr)
	}

	// GPU compute process (kể cả tên tiến trình tùy biến)
	isWorkload, attr = IsWorkloadCandidate("cuda_app", "./cuda_app", true, 4096)
	if !isWorkload || attr != AttributionVerifiedWorkload {
		t.Errorf("Kỳ vọng GPU compute app là Verified Workload, nhận được: %v, %s", isWorkload, attr)
	}

	// C++ binary do user chạy
	isWorkload, attr = IsWorkloadCandidate("simulation", "./simulation --data input.bin", false, 0)
	if !isWorkload {
		t.Errorf("Kỳ vọng './simulation' là Workload, nhận được: %v, %s", isWorkload, attr)
	}

	// Nohup script
	isWorkload, attr = IsWorkloadCandidate("sh", "nohup ./run_experiment.sh &", false, 0)
	if !isWorkload {
		t.Errorf("Kỳ vọng 'nohup ./run_experiment.sh' là Workload, nhận được: %v, %s", isWorkload, attr)
	}

	// Jupyter notebook server
	isWorkload, attr = IsWorkloadCandidate("jupyter-lab", "/usr/local/bin/jupyter-lab --port 8888", false, 0)
	if !isWorkload || attr != AttributionVerifiedWorkload {
		t.Errorf("Kỳ vọng 'jupyter-lab' là Verified Workload, nhận được: %v, %s", isWorkload, attr)
	}

	// Control plane imports / uvicorn phải luôn bị gắn AttributionSystemProcess
	isWorkload, attr = IsWorkloadCandidate("python3", "python3 -c 'from app.main import app'", false, 0)
	if isWorkload || attr != AttributionSystemProcess {
		t.Errorf("Kỳ vọng python3 import app.main là (false, AttributionSystemProcess), nhận được: %v, %s", isWorkload, attr)
	}

	if err := CheckSafeGuard(ProcessInfo{PID: 12345, PPID: 100, Name: "python3", CommandLine: "uvicorn server:app"}); err == nil {
		t.Errorf("Kỳ vọng CheckSafeGuard từ chối tiến trình uvicorn")
	}
}

func intPtr(v int) *int {
	return &v
}

func TestAggregateWorkloadTrees_RollsUpChildGPUVRAM(t *testing.T) {
	procs := []ProcessInfo{
		{
			PID:            555432,
			PPID:           1,
			User:           "vinh_test",
			Name:           "python",
			Classification: ClassResearchJob,
			IsUserWorkload: true,
			IsRootWorkload: true,
			CPUPercent:     0.0,
			MemoryRSSMB:    15.7,
			IsGPU:          false,
			VRAMMB:         0,
		},
		{
			PID:            555445,
			PPID:           555432,
			User:           "vinh_test",
			Name:           "python",
			Classification: ClassResearchJob,
			IsUserWorkload: true,
			IsRootWorkload: true,
			CPUPercent:     0.2,
			MemoryRSSMB:    100.0,
		},
		{
			PID:            772470,
			PPID:           555445,
			User:           "vinh_test",
			Name:           "python",
			Classification: ClassResearchJob,
			IsUserWorkload: true,
			IsRootWorkload: true,
			CPUPercent:     0.3,
			MemoryRSSMB:    80.0,
		},
		{
			PID:            772500,
			PPID:           772470,
			User:           "vinh_test",
			Name:           "python",
			Classification: ClassGPUProcess,
			IsUserWorkload: true,
			IsRootWorkload: true,
			CPUPercent:     1.3,
			MemoryRSSMB:    500.0,
			IsGPU:          true,
			GPUIndex:       intPtr(1),
			GPUUUID:        "GPU-1111",
			VRAMMB:         1024,
			TotalVRAMMB:    1024,
			TreeIsGPU:      true,
			TreeGPUIndex:   intPtr(1),
			TreeGPUUUID:    "GPU-1111",
		},
		{
			PID:            772511,
			PPID:           772470,
			User:           "vinh_test",
			Name:           "python",
			Classification: ClassGPUProcess,
			IsUserWorkload: true,
			IsRootWorkload: true,
			CPUPercent:     4.3,
			MemoryRSSMB:    2557.3,
			IsGPU:          true,
			GPUIndex:       intPtr(0),
			GPUUUID:        "GPU-0000",
			VRAMMB:         2960,
			TotalVRAMMB:    2960,
			TreeIsGPU:      true,
			TreeGPUIndex:   intPtr(0),
			TreeGPUUUID:    "GPU-0000",
		},
		{
			PID:            772739,
			PPID:           772511,
			User:           "vinh_test",
			Name:           "python",
			Classification: ClassResearchJob,
			IsUserWorkload: true,
			IsRootWorkload: true,
			CPUPercent:     1.0,
			MemoryRSSMB:    400.0,
		},
		{
			// Tiến trình thuộc user khác không được gộp vào cây của vinh_test
			PID:            888888,
			PPID:           555432,
			User:           "other_user",
			Name:           "python",
			Classification: ClassGPUProcess,
			IsUserWorkload: true,
			IsRootWorkload: true,
			CPUPercent:     50.0,
			MemoryRSSMB:    1024.0,
			IsGPU:          true,
			GPUIndex:       intPtr(2),
			GPUUUID:        "GPU-2222",
			VRAMMB:         8192,
			TotalVRAMMB:    8192,
			TreeIsGPU:      true,
			TreeGPUIndex:   intPtr(2),
			TreeGPUUUID:    "GPU-2222",
		},
	}

	aggregateWorkloadTrees(procs)

	root := procs[0]
	if root.ChildrenCount != 5 {
		t.Fatalf("Kỳ vọng ChildrenCount=5, nhận được: %d", root.ChildrenCount)
	}
	if root.TotalVRAMMB != 3984 {
		t.Fatalf("Kỳ vọng TotalVRAMMB=3984, nhận được: %d", root.TotalVRAMMB)
	}
	if !root.TreeIsGPU {
		t.Fatalf("Kỳ vọng TreeIsGPU=true")
	}
	if root.TreeGPUIndex == nil || *root.TreeGPUIndex != 0 {
		t.Fatalf("Kỳ vọng TreeGPUIndex=0, nhận được: %v", root.TreeGPUIndex)
	}
	if root.TreeGPUUUID != "GPU-0000" {
		t.Fatalf("Kỳ vọng TreeGPUUUID=GPU-0000, nhận được: %s", root.TreeGPUUUID)
	}
	// Không được ghi đè các trường raw trên root để tránh double-counting khi filter=all hoặc GetProcessTree
	if root.VRAMMB != 0 || root.IsGPU {
		t.Fatalf("Kỳ vọng raw VRAMMB=0 và IsGPU=false trên root, nhận được: VRAMMB=%d IsGPU=%v", root.VRAMMB, root.IsGPU)
	}
	// User khác vẫn giữ nguyên root riêng và không gộp vào cây vinh_test
	if !procs[6].IsRootWorkload || procs[6].ParentWorkloadPID != 0 {
		t.Fatalf("Tiến trình của user khác không được gộp vào cây của vinh_test")
	}
	if len(root.GPUAllocations) != 2 {
		t.Fatalf("Kỳ vọng 2 GPUAllocations tách biệt theo UUID, nhận được: %+v", root.GPUAllocations)
	}
	if root.GPUAllocations[0].GPUUUID != "GPU-0000" || root.GPUAllocations[0].VRAMMB != 2960 ||
		root.GPUAllocations[1].GPUUUID != "GPU-1111" || root.GPUAllocations[1].VRAMMB != 1024 {
		t.Fatalf("GPUAllocations rollup không chính xác: %+v", root.GPUAllocations)
	}
}

func TestClassifyWorkloadKind_ConservativeAndMonitoring(t *testing.T) {
	k, c, _ := ClassifyWorkloadKind("gpustat", "gpustat --watch", false, 0, false)
	if k != "MONITORING" || c != "VERIFIED" {
		t.Fatalf("gpustat phải là MONITORING/VERIFIED, nhận: %s/%s", k, c)
	}
	k, c, _ = ClassifyWorkloadKind("python3", "/usr/bin/python3 -m gpustat -i 1", false, 0, false)
	if k != "MONITORING" || c != "VERIFIED" {
		t.Fatalf("python -m gpustat phải là MONITORING/VERIFIED, nhận: %s/%s", k, c)
	}
	// Substring trap in unrelated directory name must NOT classify as MONITORING
	k, _, _ = ClassifyWorkloadKind("python3", "python3 /data/gpustat_export/analyze.py", false, 0, false)
	if k == "MONITORING" {
		t.Fatalf("Substring trong đường dẫn không phải executable monitoring: %s", k)
	}
	// Bare python script must NOT fabricate TRAINING
	k, c, _ = ClassifyWorkloadKind("python3", "python3 evaluate_model.py", true, 4096, false)
	if k != "GENERAL_COMPUTE" || c != "HEURISTIC" {
		t.Fatalf("python script không có proof training phải là GENERAL_COMPUTE, nhận: %s/%s", k, c)
	}
	// Torchrun -> TRAINING
	k, _, _ = ClassifyWorkloadKind("torchrun", "torchrun --nproc_per_node=2 train.py", true, 8192, false)
	if k != "TRAINING" {
		t.Fatalf("torchrun phải là TRAINING, nhận: %s", k)
	}
	// Notebook
	k, c, _ = ClassifyWorkloadKind("jupyter-lab", "/opt/conda/bin/jupyter-lab --port 8888", false, 0, false)
	if k != "NOTEBOOK" || c != "VERIFIED" {
		t.Fatalf("jupyter-lab phải là NOTEBOOK/VERIFIED, nhận: %s/%s", k, c)
	}
	// Protected system
	k, c, _ = ClassifyWorkloadKind("systemd", "/sbin/systemd", false, 0, true)
	if k != "SYSTEM" || c != "VERIFIED" {
		t.Fatalf("protected systemd phải là SYSTEM/VERIFIED, nhận: %s/%s", k, c)
	}
}

