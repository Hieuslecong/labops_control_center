package docker

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
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

	// AT-07: StopContainer phải từ chối ngay khi inspect thất bại hoặc container không được quản lý
	err = ctrl.StopContainer("postgres", 5)
	if err == nil {
		t.Error("StopContainer phải từ chối can thiệp container postgres (AT-07)")
	}

	err = ctrl.StopContainer("unmanaged_random_container", 5)
	if err == nil {
		t.Error("StopContainer phải từ chối khi không inspect được hoặc container không thuộc quản lý của LabOps (AT-07)")
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

type fakeDockerDaemon struct {
	mu           sync.Mutex
	containers   map[string]*dockerInspectJSON
	failStart    bool
	deleteCalls  int
	tamperUnsafe bool
}

func startMockDockerSocket(t *testing.T, daemon *fakeDockerDaemon) string {
	t.Helper()
	sockPath := filepath.Join(t.TempDir(), "docker.sock")
	lis, err := net.Listen("unix", sockPath)
	if err != nil {
		t.Fatalf("failed to listen on unix socket: %v", err)
	}
	t.Cleanup(func() { _ = lis.Close() })

	canonicalCreatedID := strings.Repeat("a1", 32)
	mux := http.NewServeMux()
	mux.HandleFunc("/v1.45/containers/create", func(w http.ResponseWriter, r *http.Request) {
		name := r.URL.Query().Get("name")
		var payload struct {
			Image      string            `json:"Image"`
			User       string            `json:"User"`
			Labels     map[string]string `json:"Labels"`
			HostConfig struct {
				Privileged  bool     `json:"Privileged"`
				Memory      int64    `json:"Memory"`
				NanoCpus    int64    `json:"NanoCpus"`
				CpuShares   int64    `json:"CpuShares"`
				PidsLimit   int64    `json:"PidsLimit"`
				CapDrop     []string `json:"CapDrop"`
				SecurityOpt []string `json:"SecurityOpt"`
				NetworkMode string   `json:"NetworkMode"`
				Binds       []string `json:"Binds"`
			} `json:"HostConfig"`
		}
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		daemon.mu.Lock()
		defer daemon.mu.Unlock()
		for _, c := range daemon.containers {
			if strings.TrimPrefix(c.Name, "/") == name {
				http.Error(w, "Conflict", http.StatusConflict)
				return
			}
		}
		ins := &dockerInspectJSON{
			ID:      canonicalCreatedID,
			Name:    "/" + name,
			Image:   "sha256:" + strings.Repeat("b2", 32),
			Created: time.Now().UTC().Format(time.RFC3339),
		}
		ins.Config.Image = payload.Image
		ins.Config.Labels = payload.Labels
		ins.Config.User = payload.User
		pids := payload.HostConfig.PidsLimit
		ins.HostConfig.Privileged = daemon.tamperUnsafe || payload.HostConfig.Privileged
		ins.HostConfig.Memory = payload.HostConfig.Memory
		ins.HostConfig.NanoCPUs = payload.HostConfig.NanoCpus
		ins.HostConfig.CpuShares = payload.HostConfig.CpuShares
		ins.HostConfig.PidsLimit = &pids
		ins.HostConfig.CapDrop = payload.HostConfig.CapDrop
		ins.HostConfig.SecurityOpt = payload.HostConfig.SecurityOpt
		ins.HostConfig.NetworkMode = payload.HostConfig.NetworkMode
		for _, b := range payload.HostConfig.Binds {
			parts := strings.Split(b, ":")
			if len(parts) >= 2 {
				ins.Mounts = append(ins.Mounts, struct {
					Type        string `json:"Type"`
					Source      string `json:"Source"`
					Destination string `json:"Destination"`
					RW          bool   `json:"RW"`
				}{Type: "bind", Source: parts[0], Destination: parts[1], RW: true})
			}
		}
		ins.State.Status = "created"
		daemon.containers[canonicalCreatedID] = ins
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(map[string]string{"Id": canonicalCreatedID})
	})

	mux.HandleFunc("/v1.45/containers/", func(w http.ResponseWriter, r *http.Request) {
		rest := strings.TrimPrefix(r.URL.Path, "/v1.45/containers/")
		parts := strings.Split(rest, "/")
		idOrName := parts[0]
		action := ""
		if len(parts) > 1 {
			action = parts[1]
		}
		daemon.mu.Lock()
		defer daemon.mu.Unlock()
		var target *dockerInspectJSON
		for _, c := range daemon.containers {
			if c.ID == idOrName || strings.TrimPrefix(c.Name, "/") == idOrName {
				target = c
				break
			}
		}
		if r.Method == http.MethodDelete {
			daemon.deleteCalls++
			if target == nil {
				http.NotFound(w, r)
				return
			}
			delete(daemon.containers, target.ID)
			w.WriteHeader(http.StatusNoContent)
			return
		}
		if target == nil {
			http.NotFound(w, r)
			return
		}
		switch action {
		case "json":
			_ = json.NewEncoder(w).Encode(target)
		case "start":
			if daemon.failStart {
				http.Error(w, "simulated start failure", http.StatusInternalServerError)
				return
			}
			target.State.Status = "running"
			target.State.Running = true
			w.WriteHeader(http.StatusNoContent)
		case "stop":
			target.State.Status = "exited"
			target.State.Running = false
			w.WriteHeader(http.StatusNoContent)
		default:
			http.NotFound(w, r)
		}
	})

	srv := &http.Server{Handler: mux}
	go func() { _ = srv.Serve(lis) }()
	t.Cleanup(func() { _ = srv.Close() })
	return sockPath
}

func TestTrustedRegistryAndDockerProvisioningMatrix(t *testing.T) {
	ctx := context.Background()
	validDigest := "sha256:" + strings.Repeat("b2", 32)
	validImage := "ghcr.io/aiotlab/workspace@" + validDigest
	cidVictim := strings.Repeat("cd", 32)
	cidFakeWS := strings.Repeat("ef", 32)

	baseStorageDir := filepath.Join(t.TempDir(), "workspaces", "alice")
	if err := os.MkdirAll(baseStorageDir, 0750); err != nil {
		t.Fatal(err)
	}

	pids := int64(2048)
	daemon := &fakeDockerDaemon{
		containers: map[string]*dockerInspectJSON{
			cidVictim: {
				ID:   cidVictim,
				Name: "/ws-alice",
				Image: validDigest,
				HostConfig: struct {
					Privileged     bool     `json:"Privileged"`
					Memory         int64    `json:"Memory"`
					MemorySwap     int64    `json:"MemorySwap"`
					NanoCPUs       int64    `json:"NanoCpus"`
					CpuShares      int64    `json:"CpuShares"`
					PidsLimit      *int64   `json:"PidsLimit"`
					CapDrop        []string `json:"CapDrop"`
					CapAdd         []string `json:"CapAdd"`
					SecurityOpt    []string `json:"SecurityOpt"`
					PidMode        string   `json:"PidMode"`
					NetworkMode    string   `json:"NetworkMode"`
					ReadonlyRootfs bool     `json:"ReadonlyRootfs"`
					PortBindings   map[string][]struct {
						HostIP   string `json:"HostIp"`
						HostPort string `json:"HostPort"`
					} `json:"PortBindings"`
				}{
					Memory: 8 * 1024 * 1024 * 1024, NanoCPUs: 4e9, PidsLimit: &pids,
					CapDrop: []string{"ALL"}, SecurityOpt: []string{"no-new-privileges:true"},
				},
				Config: struct {
					Image  string            `json:"Image"`
					Labels map[string]string `json:"Labels"`
					User   string            `json:"User"`
				}{
					Image: validImage,
					Labels: map[string]string{
						"labops.managed":      "true",
						"labops.workspace_id": "ws-id-alice",
						"labops.member_id":    "mem-alice",
						"labops.member":       "alice",
						"labops.node_id":      "workstation",
						"labops.generation":   "1",
					},
				},
			},
			cidFakeWS: {
				ID:   cidFakeWS,
				Name: "/ws-spoofed",
				Config: struct {
					Image  string            `json:"Image"`
					Labels map[string]string `json:"Labels"`
					User   string            `json:"User"`
				}{
					Labels: map[string]string{"labops.managed": "true", "labops.workspace_id": "ws-unknown"},
				},
			},
		},
	}
	sockPath := startMockDockerSocket(t, daemon)

	reg := NewInMemoryRegistry()
	if err := reg.UpsertRecord(WorkspaceRecord{
		WorkspaceID:          "ws-id-alice",
		Name:                 "ws-alice",
		MemberID:             "mem-alice",
		MemberUsername:       "alice",
		NodeID:               "workstation",
		CanonicalContainerID: cidVictim,
		ImageDigest:          validDigest,
		Generation:           1,
	}); err != nil {
		t.Fatal(err)
	}

	ctrl := NewControllerWithRegistry(sockPath, "workstation", reg)

	// 1 & 2. Unknown container & fake ws-* container rejected
	if err := ctrl.StopContainer(cidFakeWS, 5); err == nil {
		t.Fatal("1/2: Fake ws- container MUST be rejected")
	}
	if _, err := ctrl.InspectWorkspace(ctx, "ws-spoofed"); err == nil {
		t.Fatal("1/2: Unknown workspace MUST be rejected on inspect")
	}

	// 3. Label spoof rejected
	daemon.mu.Lock()
	daemon.containers[cidVictim].Config.Labels["labops.generation"] = "999"
	daemon.mu.Unlock()
	if err := ctrl.StopWorkspaceContainer(ctx, "ws-id-alice", cidVictim, 5); err == nil {
		t.Fatal("3: Spoofed generation label MUST be rejected")
	}
	daemon.mu.Lock()
	daemon.containers[cidVictim].Config.Labels["labops.generation"] = "1"
	daemon.mu.Unlock()

	// 4. Wrong owner rejected
	daemon.mu.Lock()
	daemon.containers[cidVictim].Config.Labels["labops.member"] = "eve"
	daemon.mu.Unlock()
	if err := ctrl.StopWorkspaceContainer(ctx, "ws-id-alice", cidVictim, 5); err == nil {
		t.Fatal("4: Wrong owner label MUST be rejected")
	}
	daemon.mu.Lock()
	daemon.containers[cidVictim].Config.Labels["labops.member"] = "alice"
	daemon.mu.Unlock()

	// 5. Container replacement rejected
	otherID := strings.Repeat("99", 32)
	daemon.mu.Lock()
	replaced := *daemon.containers[cidVictim]
	replaced.ID = otherID
	daemon.containers[otherID] = &replaced
	daemon.mu.Unlock()
	if err := ctrl.StopWorkspaceContainer(ctx, "ws-id-alice", otherID, 5); err == nil {
		t.Fatal("5: Replaced container ID != registry canonical ID MUST be rejected")
	}

	// 6. Node mismatch rejected
	wrongNodeCtrl := NewControllerWithRegistry(sockPath, "track", reg)
	if err := wrongNodeCtrl.StopWorkspaceContainer(ctx, "ws-id-alice", cidVictim, 5); err == nil {
		t.Fatal("6: Node mismatch MUST be rejected")
	}

	// 7. Corrupt / unavailable registry rejected
	noRegCtrl := NewControllerWithRegistry(sockPath, "workstation", nil)
	if err := noRegCtrl.StopWorkspaceContainer(ctx, "ws-id-alice", cidVictim, 5); err == nil {
		t.Fatal("7a: Nil registry MUST be rejected")
	}
	corruptRegFile := filepath.Join(t.TempDir(), "corrupt_reg.json")
	_ = os.WriteFile(corruptRegFile, []byte("{bad json"), 0600)
	corruptReg, _ := NewTrustedRegistry(corruptRegFile)
	corruptCtrl := NewControllerWithRegistry(sockPath, "workstation", corruptReg)
	if _, err := corruptCtrl.ListWorkspaces(ctx); err == nil {
		t.Fatal("7b: Corrupt registry MUST reject ListWorkspaces fail-closed")
	}

	// 8. Existing container preserved on ProvisionWorkspace conflict (zero DELETE calls!)
	if _, err := ctrl.ProvisionWorkspace(ctx, ProvisionRequest{
		WorkspaceID: "ws-new", Name: "ws-alice", MemberID: "mem-bob", MemberUsername: "bob",
		UID: 1005, GID: 1005, ImageRef: validImage, ImageDigest: validDigest,
		StorageRoot: baseStorageDir, CPULimitCores: 4, RAMLimitMB: 4096, PidsLimit: 1024,
	}); err == nil || !strings.Contains(err.Error(), "ERR_CONTAINER_EXISTS_PRESERVED") {
		t.Fatalf("8: Expected ERR_CONTAINER_EXISTS_PRESERVED, got: %v", err)
	}
	daemon.mu.Lock()
	if daemon.deleteCalls != 0 {
		t.Fatalf("8: Existing container MUST NOT be auto-deleted; got %d delete calls", daemon.deleteCalls)
	}
	daemon.mu.Unlock()

	// 9. Mutable / invalid image rejected
	if _, err := ctrl.ProvisionWorkspace(ctx, ProvisionRequest{
		WorkspaceID: "ws-mut", Name: "ws-mutable", MemberID: "mem-bob", MemberUsername: "bob",
		UID: 1005, GID: 1005, ImageRef: "labops-workspace:latest", ImageDigest: "",
		StorageRoot: baseStorageDir, CPULimitCores: 4, RAMLimitMB: 4096, PidsLimit: 1024,
	}); err == nil {
		t.Fatal("9: Mutable :latest image MUST be rejected")
	}

	// 10. Partial create recoverable (start fails -> ObservedState NOT ACTIVE, canonical ID saved)
	daemon.mu.Lock()
	daemon.failStart = true
	daemon.mu.Unlock()
	bobDir := filepath.Join(t.TempDir(), "bob-ws")
	partialRec, partErr := ctrl.ProvisionWorkspace(ctx, ProvisionRequest{
		WorkspaceID: "ws-part", Name: "ws-part", MemberID: "mem-bob", MemberUsername: "bob",
		UID: 1005, GID: 1005, ImageRef: validImage, ImageDigest: validDigest,
		StorageRoot: bobDir, CPULimitCores: 4, RAMLimitMB: 4096, PidsLimit: 1024,
	})
	if partErr == nil {
		t.Fatal("10: Expected error when start fails")
	}
	if partialRec == nil || partialRec.ObservedState == "ACTIVE" || !IsCanonicalContainerID(partialRec.CanonicalContainerID) {
		t.Fatalf("10: Expected recoverable non-ACTIVE record with canonical ID, got: %+v", partialRec)
	}

	// 11. Safety violation prevents false ACTIVE, while clean start marks ACTIVE
	daemon.mu.Lock()
	daemon.failStart = false
	delete(daemon.containers, strings.Repeat("a1", 32))
	daemon.tamperUnsafe = true
	daemon.mu.Unlock()
	unsafeRec, unsafeErr := ctrl.ProvisionWorkspace(ctx, ProvisionRequest{
		WorkspaceID: "ws-unsafe", Name: "ws-unsafe", MemberID: "mem-carol", MemberUsername: "carol",
		UID: 1006, GID: 1006, ImageRef: validImage, ImageDigest: validDigest,
		StorageRoot: bobDir, CPULimitCores: 4, RAMLimitMB: 4096, PidsLimit: 1024,
	})
	if unsafeErr == nil || (unsafeRec != nil && unsafeRec.ObservedState == "ACTIVE") {
		t.Fatalf("11a: Unsafe container MUST NOT report ACTIVE, rec=%+v err=%v", unsafeRec, unsafeErr)
	}

	// Clean provision succeeds as ACTIVE
	daemon.mu.Lock()
	delete(daemon.containers, strings.Repeat("a1", 32))
	daemon.tamperUnsafe = false
	daemon.mu.Unlock()
	okRec, okErr := ctrl.ProvisionWorkspace(ctx, ProvisionRequest{
		WorkspaceID: "ws-clean", Name: "ws-clean", MemberID: "mem-carol", MemberUsername: "carol",
		UID: 1006, GID: 1006, ImageRef: validImage, ImageDigest: validDigest,
		StorageRoot: bobDir, SSHPort: 22010, CPULimitCores: 4, RAMLimitMB: 4096, PidsLimit: 1024,
	})
	if okErr != nil || okRec.ObservedState != "ACTIVE" {
		t.Fatalf("11b: Clean hardened provision failed: err=%v rec=%+v", okErr, okRec)
	}

	// 12. Shared Weighted CPU & Dynamic Storage Provisioning
	daemon.mu.Lock()
	delete(daemon.containers, strings.Repeat("a1", 32))
	daemon.mu.Unlock()
	sharedRec, sharedErr := ctrl.ProvisionWorkspace(ctx, ProvisionRequest{
		WorkspaceID:        "ws-shared",
		Name:               "ws-shared",
		MemberID:           "mem-dave",
		MemberUsername:     "dave",
		UID:                1007,
		GID:                1007,
		ImageRef:           validImage,
		ImageDigest:        validDigest,
		MemberStorageDir:   bobDir,
		ContainerMountPath: "/workspace",
		ResourceMode:       "SHARED_WEIGHTED",
		CPUWeight:          100,
		RAMLimitMB:         8192,
		PidsLimit:          4096,
		SSHPort:            22011,
	})
	if sharedErr != nil || sharedRec.ObservedState != "ACTIVE" {
		t.Fatalf("12: Shared weighted provision failed: err=%v rec=%+v", sharedErr, sharedRec)
	}
	if sharedRec.ResourceMode != "SHARED_WEIGHTED" || sharedRec.CPUWeight != 100 {
		t.Fatalf("12: ResourceMode or CPUWeight mismatch: rec=%+v", sharedRec)
	}
}

