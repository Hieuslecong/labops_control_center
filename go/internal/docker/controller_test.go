package docker

import (
	"context"
	"testing"
	"time"
)

func TestDockerController_ProtectionRules(t *testing.T) {
	ctrl := NewController("/var/run/docker.sock")

	// 1. Kiểm tra cấm can thiệp container hệ thống
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	_, err := ctrl.InspectWorkspace(ctx, "labops-wg-easy")
	if err == nil {
		t.Error("InspectWorkspace phải chặn can thiệp container 'labops-wg-easy'")
	}

	_, err = ctrl.InspectWorkspace(ctx, "postgres")
	if err == nil {
		t.Error("InspectWorkspace phải chặn can thiệp container 'postgres'")
	}
}

func TestDockerController_ListRealWorkspaces(t *testing.T) {
	ctrl := NewController("/var/run/docker.sock")

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	workspaces, err := ctrl.ListWorkspaces(ctx)
	if err != nil {
		t.Skipf("Bỏ qua nếu docker socket không kết nối được: %v", err)
	}

	for _, ws := range workspaces {
		// Đảm bảo không container hệ thống nào lọt vào danh sách
		if ProtectedSystemContainers[ws.Name] {
			t.Errorf("Container hệ thống '%s' không được nằm trong danh sách workspaces", ws.Name)
		}
	}
}

