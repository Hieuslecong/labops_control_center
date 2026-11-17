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
	if os.Getenv("LABOPS_ENABLE_LIVE_TESTS") != "1" {
		t.Skip("Agent endpoint integration test queries live host, processes, and GPU telemetry")
	}
	cfg := &config.AgentConfig{
		ServerID:         "track",
		ServerName:       "Test Server",
		Hostname:         "aiotlab-rack",
		BindAddr:         "127.0.0.1",
		Port:             9100,
		SecretToken:      "test-token",
		RepoGuardDataDir: t.TempDir(),
	}

	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	hostCol := host.NewCollector(cfg)
	gpuCol := gpu.NewCollector()
	procCol := process.NewCollector(cfg.ServerID, gpuCol)
	dockerCtrl := docker.NewController(filepath.Join(t.TempDir(), "fake-docker.sock"))

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

	// Read endpoints are authenticated too; do not let a no-token GET invoke Docker or repository state.
	reqUnauthRead := httptest.NewRequest("GET", "/api/v1/agent/telemetry/full", nil)
	wUnauthRead := httptest.NewRecorder()
	srv.httpSrv.Handler.ServeHTTP(wUnauthRead, reqUnauthRead)
	if wUnauthRead.Code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated GET must be rejected, got %d", wUnauthRead.Code)
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
}

func TestAgentAuthMiddlewareRequiresSecretForAllNonHealthRoutes(t *testing.T) {
	for _, secret := range []string{"", "test-secret"} {
		srv := &Server{cfg: &config.AgentConfig{SecretToken: secret}}
		h := srv.authMiddleware(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) }))
		for _, path := range []string{"/api/v1/agent/host", "/api/v1/agent/repositoryguard/requests"} {
			req := httptest.NewRequest(http.MethodGet, path, nil)
			w := httptest.NewRecorder()
			h.ServeHTTP(w, req)
			expected := http.StatusUnauthorized
			if path == "/api/v1/agent/repositoryguard/requests" && secret == "" {
				expected = http.StatusServiceUnavailable
			}
			if w.Code != expected {
				t.Fatalf("secret=%q path=%s got %d", secret, path, w.Code)
			}
		}
		req := httptest.NewRequest(http.MethodGet, "/health", nil)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, req)
		if w.Code != http.StatusNoContent {
			t.Fatalf("health exemption failed: %d", w.Code)
		}
	}
}

func TestRepositoryGuardRequiresBothServiceAndScopedCredentials(t *testing.T) {
	cfg := &config.AgentConfig{SecretToken: "service-secret", RepositoryTokenSecret: strings.Repeat("k", 32)}
	srv := &Server{cfg: cfg}
	h := srv.authMiddleware(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) }))

	request := httptest.NewRequest(http.MethodGet, "/api/v1/agent/repositoryguard/requests", nil)
	request.Header.Set("Authorization", "Bearer forged")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, request)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("scoped token without service credential got %d", w.Code)
	}

	request = httptest.NewRequest(http.MethodGet, "/api/v1/agent/repositoryguard/requests", nil)
	request.Header.Set("X-LabOps-Agent-Secret", "service-secret")
	request.Header.Set("Authorization", "Bearer forged")
	w = httptest.NewRecorder()
	h.ServeHTTP(w, request)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("service credential without valid scoped capability got %d", w.Code)
	}
}

func TestRepositoryGuardUnavailableIsExplicitServiceUnavailable(t *testing.T) {
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/agent/repositoryguard/requests", nil)
	repositoryGuardUnavailable(recorder, request)
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("unconfigured Repository Guard status = %d, want %d", recorder.Code, http.StatusServiceUnavailable)
	}
	if !strings.Contains(recorder.Body.String(), "durable storage") {
		t.Fatalf("unconfigured Repository Guard response lacks its reason: %q", recorder.Body.String())
	}
}
