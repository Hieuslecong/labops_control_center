package transport

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Hieuslecong/labops_control_center/go/internal/config"
	"github.com/Hieuslecong/labops_control_center/go/internal/docker"
	"github.com/Hieuslecong/labops_control_center/go/internal/gpu"
	"github.com/Hieuslecong/labops_control_center/go/internal/host"
	"github.com/Hieuslecong/labops_control_center/go/internal/process"
)

func TestServer_Endpoints(t *testing.T) {
	cfg := &config.AgentConfig{
		ServerID:    "track",
		ServerName:  "Test Server",
		Hostname:    "aiotlab-rack",
		BindAddr:    "127.0.0.1",
		Port:        9100,
		SecretToken: "test-token",
	}

	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	hostCol := host.NewCollector(cfg)
	gpuCol := gpu.NewCollector()
	procCol := process.NewCollector(cfg.ServerID, gpuCol)
	dockerCtrl := docker.NewController("")

	srv := NewServer(cfg, hostCol, gpuCol, procCol, dockerCtrl, logger)

	// 1. Test /health
	req := httptest.NewRequest("GET", "/health", nil)
	w := httptest.NewRecorder()
	srv.httpSrv.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("Kỳ vọng status 200 tại /health, nhận được: %d", w.Code)
	}

	var healthRes map[string]interface{}
	if err := json.NewDecoder(w.Body).Decode(&healthRes); err != nil {
		t.Fatalf("Lỗi decode json /health: %v", err)
	}
	if healthRes["status"] != "HEALTHY" {
		t.Errorf("Kỳ vọng status HEALTHY, nhận được: %v", healthRes["status"])
	}

	// 2. Test /api/v1/agent/host
	reqHost := httptest.NewRequest("GET", "/api/v1/agent/host", nil)
	reqHost.Header.Set("X-LabOps-Agent-Secret", "test-token")
	wHost := httptest.NewRecorder()
	srv.httpSrv.Handler.ServeHTTP(wHost, reqHost)

	if wHost.Code != http.StatusOK {
		t.Errorf("Kỳ vọng status 200 tại /api/v1/agent/host, nhận được: %d", wHost.Code)
	}

	var hostRes host.HostTelemetry
	if err := json.NewDecoder(wHost.Body).Decode(&hostRes); err != nil {
		t.Fatalf("Lỗi decode json /api/v1/agent/host: %v", err)
	}
	if hostRes.ServerID != "track" {
		t.Errorf("Kỳ vọng ServerID track, nhận: %s", hostRes.ServerID)
	}

	// 3. Test /api/v1/agent/telemetry/full
	reqFull := httptest.NewRequest("GET", "/api/v1/agent/telemetry/full", nil)
	reqFull.Header.Set("X-LabOps-Agent-Secret", "test-token")
	wFull := httptest.NewRecorder()
	srv.httpSrv.Handler.ServeHTTP(wFull, reqFull)

	if wFull.Code != http.StatusOK {
		t.Errorf("Kỳ vọng status 200 tại /api/v1/agent/telemetry/full, nhận được: %d", wFull.Code)
	}

	// 4. Test /api/v1/agent/processes
	reqProc := httptest.NewRequest("GET", "/api/v1/agent/processes?filter=all", nil)
	reqProc.Header.Set("X-LabOps-Agent-Secret", "test-token")
	wProc := httptest.NewRecorder()
	srv.httpSrv.Handler.ServeHTTP(wProc, reqProc)

	if wProc.Code != http.StatusOK {
		t.Errorf("Kỳ vọng status 200 tại /api/v1/agent/processes, nhận được: %d", wProc.Code)
	}

	// 5. Test /api/v1/agent/processes/terminate rejection on protected process PID 1
	termPayload := `{"ref":"track:host:1:100","timeout_seconds":5,"terminate_tree":false}`
	reqTerm := httptest.NewRequest("POST", "/api/v1/agent/processes/terminate", strings.NewReader(termPayload))
	reqTerm.Header.Set("Content-Type", "application/json")
	wTerm := httptest.NewRecorder()
	srv.httpSrv.Handler.ServeHTTP(wTerm, reqTerm)

	// Should reject because PID 1 is protected or ticks mismatch
	if wTerm.Code == http.StatusOK {
		t.Errorf("Kỳ vọng từ chối dừng PID 1, nhưng nhận được HTTP 200")
	}

	// 6. Test AT-06: Localhost request không có token tới mutation endpoint BẮT BUỘC bị từ chối 401
	reqLocalhostMut := httptest.NewRequest("POST", "/api/v1/agent/processes/terminate", strings.NewReader(termPayload))
	reqLocalhostMut.RemoteAddr = "127.0.0.1:45678"
	reqLocalhostMut.Header.Set("Content-Type", "application/json")
	wLocalhostMut := httptest.NewRecorder()
	srv.httpSrv.Handler.ServeHTTP(wLocalhostMut, reqLocalhostMut)
	if wLocalhostMut.Code != http.StatusUnauthorized {
		t.Errorf("AT-06 Vi phạm: Localhost request không có token phải bị từ chối 401, nhận: %d", wLocalhostMut.Code)
	}

	// 7. Test Fail-Closed when SecretToken is empty (GET and POST must reject with 503)
	emptyCfg := &config.AgentConfig{
		ServerID:    "workstation",
		ServerName:  "FailClosed Test",
		Hostname:    "aiot-ws1",
		BindAddr:    "127.0.0.1",
		Port:        9101,
		SecretToken: "",
	}
	emptySrv := NewServer(emptyCfg, host.NewCollector(emptyCfg), gpuCol, procCol, dockerCtrl, logger)
	reqGetEmpty := httptest.NewRequest("GET", "/api/v1/agent/host", nil)
	wGetEmpty := httptest.NewRecorder()
	emptySrv.httpSrv.Handler.ServeHTTP(wGetEmpty, reqGetEmpty)
	if wGetEmpty.Code != http.StatusServiceUnavailable {
		t.Errorf("Kỳ vọng 503 fail-closed khi thiếu LABOPS_AGENT_SECRET, nhận: %d", wGetEmpty.Code)
	}

	// 8. Test unified POST /api/v1/agent/execute allowlist, auth & INSPECT_HOST
	unauthExec := httptest.NewRequest("POST", "/api/v1/agent/execute", strings.NewReader(`{"action":"INSPECT_HOST"}`))
	wUnauthExec := httptest.NewRecorder()
	srv.httpSrv.Handler.ServeHTTP(wUnauthExec, unauthExec)
	if wUnauthExec.Code != http.StatusUnauthorized {
		t.Errorf("Expected 401 for unauthenticated execute, got %d", wUnauthExec.Code)
	}

	disallowedExec := httptest.NewRequest("POST", "/api/v1/agent/execute", strings.NewReader(`{"action":"RUN_ARBITRARY_SHELL","params":{"cmd":"rm -rf /"}}`))
	disallowedExec.Header.Set("X-LabOps-Agent-Secret", "test-token")
	wDis := httptest.NewRecorder()
	srv.httpSrv.Handler.ServeHTTP(wDis, disallowedExec)
	if wDis.Code != http.StatusForbidden {
		t.Errorf("Expected 403 for non-allowlisted action, got %d (%s)", wDis.Code, wDis.Body.String())
	}

	inspectExec := httptest.NewRequest("POST", "/api/v1/agent/execute", strings.NewReader(`{"action":"INSPECT_HOST","idempotency_key":"k-inspect-1","request_id":"req-insp-1"}`))
	inspectExec.Header.Set("X-LabOps-Agent-Secret", "test-token")
	wInsp := httptest.NewRecorder()
	srv.httpSrv.Handler.ServeHTTP(wInsp, inspectExec)
	if wInsp.Code != http.StatusOK {
		t.Fatalf("Expected 200 for allowlisted INSPECT_HOST, got %d: %s", wInsp.Code, wInsp.Body.String())
	}

	// 9. Test Idempotency conflict on mismatched parameters
	conflictExec := httptest.NewRequest("POST", "/api/v1/agent/execute", strings.NewReader(`{"action":"INSPECT_HOST","idempotency_key":"k-inspect-1","params":{"extra":"1"}}`))
	conflictExec.Header.Set("X-LabOps-Agent-Secret", "test-token")
	wConf := httptest.NewRecorder()
	srv.httpSrv.Handler.ServeHTTP(wConf, conflictExec)
	if wConf.Code != http.StatusBadRequest {
		t.Errorf("Expected 400 IDEMPOTENCY_CONFLICT, got %d (%s)", wConf.Code, wConf.Body.String())
	}

	// 10. Test /api/v1/agent/execute PROVISION_DOCKER_WORKSPACE fail-closed when Docker unavailable
	regPath := filepath.Join(t.TempDir(), "reg.json")
	reg, _ := docker.NewTrustedRegistry(regPath)
	dc := docker.NewControllerWithRegistry(filepath.Join(t.TempDir(), "missing.sock"), "workstation", reg)
	dc.SetRuntimeRoot(t.TempDir())
	wsCfg := &config.AgentConfig{ServerID: "workstation", Hostname: "aiot-ws1", BindAddr: "127.0.0.1", Port: 9102, SecretToken: "test-token"}
	wsSrv := NewServer(wsCfg, host.NewCollector(wsCfg), gpuCol, procCol, dc, logger)
	storageDir := t.TempDir()
	_ = os.MkdirAll(storageDir, 0700)
	provBody := `{"action":"PROVISION_DOCKER_WORKSPACE","node_id":"workstation","parameters":{"workspace_id":"ws-t2","name":"ws-testacc2","member_id":"m-t2","owner":"testacc2","uid":"20004","gid":"20004","image_ref":"ghcr.io/aiotlab/ws@sha256:` + strings.Repeat("a", 64) + `","member_storage_dir":"` + storageDir + `","ssh_port":"22004","resource_mode":"SHARED_WEIGHTED","cpu_weight":"100","ram_limit_mb":"4096","pids_limit":"1024"}}`
	reqProv := httptest.NewRequest("POST", "/api/v1/agent/execute", strings.NewReader(provBody))
	reqProv.Header.Set("X-LabOps-Agent-Secret", "test-token")
	wProv := httptest.NewRecorder()
	wsSrv.httpSrv.Handler.ServeHTTP(wProv, reqProv)
	if wProv.Code == http.StatusOK {
		t.Fatalf("Expected fail-closed non-200 when Docker daemon unavailable, got 200: %s", wProv.Body.String())
	}
}
