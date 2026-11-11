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
}

