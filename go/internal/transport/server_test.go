package transport

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
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
	procCol := process.NewCollector(cfg.ServerID)
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
}
