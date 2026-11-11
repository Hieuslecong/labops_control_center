package transport

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/Hieuslecong/labops_control_center/go/internal/config"
	"github.com/Hieuslecong/labops_control_center/go/internal/docker"
	"github.com/Hieuslecong/labops_control_center/go/internal/gpu"
	"github.com/Hieuslecong/labops_control_center/go/internal/host"
	"github.com/Hieuslecong/labops_control_center/go/internal/process"
)

// Server quản lý HTTP API cục bộ của Node Agent
type Server struct {
	cfg        *config.AgentConfig
	hostCol    *host.Collector
	gpuCol     *gpu.Collector
	procCol    *process.Collector
	procCtrl   *process.Controller
	dockerCtrl *docker.Controller
	httpSrv    *http.Server
	logger     *slog.Logger
}

// NewServer khởi tạo HTTP Server cho Agent
func NewServer(
	cfg *config.AgentConfig,
	hostCol *host.Collector,
	gpuCol *gpu.Collector,
	procCol *process.Collector,
	dockerCtrl *docker.Controller,
	logger *slog.Logger,
) *Server {
	s := &Server{
		cfg:        cfg,
		hostCol:    hostCol,
		gpuCol:     gpuCol,
		procCol:    procCol,
		procCtrl:   process.NewController(procCol),
		dockerCtrl: dockerCtrl,
		logger:     logger,
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/health", s.handleHealth)
	mux.HandleFunc("/api/v1/agent/host", s.handleHostTelemetry)
	mux.HandleFunc("/api/v1/agent/gpus", s.handleGPUTelemetry)
	mux.HandleFunc("/api/v1/agent/processes", s.handleProcessTelemetry)
	mux.HandleFunc("/api/v1/agent/processes/tree", s.handleProcessTree)
	mux.HandleFunc("/api/v1/agent/processes/terminate", s.handleProcessTerminate)
	mux.HandleFunc("/api/v1/agent/processes/kill", s.handleProcessKill)
	mux.HandleFunc("/api/v1/agent/workspaces", s.handleWorkspaces)
	mux.HandleFunc("/api/v1/agent/telemetry/full", s.handleFullTelemetry)

	addr := fmt.Sprintf("%s:%d", cfg.BindAddr, cfg.Port)
	s.httpSrv = &http.Server{
		Addr:         addr,
		Handler:      s.authMiddleware(mux),
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 15 * time.Second,
	}

	return s
}

// authMiddleware xác thực token nếu có cấu hình
func (s *Server) authMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/health" {
			next.ServeHTTP(w, r)
			return
		}

		clientIP := r.RemoteAddr
		if strings.HasPrefix(clientIP, "127.0.0.1") || strings.HasPrefix(clientIP, "[::1]") {
			next.ServeHTTP(w, r)
			return
		}

		token := r.Header.Get("X-LabOps-Agent-Secret")
		if token == "" {
			authHeader := r.Header.Get("Authorization")
			if strings.HasPrefix(authHeader, "Bearer ") {
				token = strings.TrimPrefix(authHeader, "Bearer ")
			}
		}

		if token != s.cfg.SecretToken {
			http.Error(w, `{"error":"Unauthorized: invalid or missing agent token"}`, http.StatusUnauthorized)
			return
		}

		next.ServeHTTP(w, r)
	})
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":    "HEALTHY",
		"server_id": s.cfg.ServerID,
		"hostname":  s.cfg.Hostname,
		"time":      time.Now().UTC().Format(time.RFC3339),
		"agent":     "labops-go-agent-v2",
	})
}

func (s *Server) handleHostTelemetry(w http.ResponseWriter, r *http.Request) {
	telemetry := s.hostCol.GetLatest()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(telemetry)
}

func (s *Server) handleGPUTelemetry(w http.ResponseWriter, r *http.Request) {
	gpus := s.gpuCol.GetLatest()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(gpus)
}

func (s *Server) handleProcessTelemetry(w http.ResponseWriter, r *http.Request) {
	filter := r.URL.Query().Get("filter")
	if filter == "" {
		filter = "all"
	}
	procs := s.procCol.Collect(filter)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(procs)
}

func (s *Server) handleProcessTree(w http.ResponseWriter, r *http.Request) {
	ref := r.URL.Query().Get("ref")
	if ref == "" {
		http.Error(w, `{"error":"Thiếu tham số 'ref'"}`, http.StatusBadRequest)
		return
	}

	tree, err := s.procCol.GetProcessTree(ref)
	if err != nil {
		s.logger.Warn("Lỗi tìm cây tiến trình", "ref", ref, "err", err)
		http.Error(w, fmt.Sprintf(`{"error":"%s"}`, err.Error()), http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(tree)
}

type terminateRequest struct {
	Ref            string `json:"ref"`
	TimeoutSeconds int    `json:"timeout_seconds"`
	TerminateTree  bool   `json:"terminate_tree"`
}

func (s *Server) handleProcessTerminate(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, `{"error":"Method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	var req terminateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, `{"error":"Invalid JSON payload"}`, http.StatusBadRequest)
		return
	}

	if req.Ref == "" {
		http.Error(w, `{"error":"Thiếu tham số 'ref'"}`, http.StatusBadRequest)
		return
	}

	result, err := s.procCtrl.TerminateProcess(req.Ref, req.TimeoutSeconds, req.TerminateTree)
	if err != nil {
		s.logger.Warn("Từ chối hoặc thất bại khi dừng tiến trình", "ref", req.Ref, "err", err)
		status := http.StatusInternalServerError
		errStr := err.Error()
		if strings.Contains(errStr, "PROTECTED_PROCESS_DENIED") {
			status = http.StatusForbidden
		} else if strings.Contains(errStr, "PID_RECYCLED") {
			status = http.StatusConflict
		} else if strings.Contains(errStr, "PROCESS_NOT_FOUND") {
			status = http.StatusNotFound
		} else if strings.Contains(errStr, "INVALID_PROCESS_REF") {
			status = http.StatusBadRequest
		}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error":  errStr,
			"status": "FAILED",
		})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(result)
}

type killRequest struct {
	Ref      string `json:"ref"`
	Force    bool   `json:"force"`
	KillTree bool   `json:"kill_tree"`
}

func (s *Server) handleProcessKill(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, `{"error":"Method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	var req killRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, `{"error":"Invalid JSON payload"}`, http.StatusBadRequest)
		return
	}

	if req.Ref == "" {
		http.Error(w, `{"error":"Thiếu tham số 'ref'"}`, http.StatusBadRequest)
		return
	}

	result, err := s.procCtrl.KillProcess(req.Ref, req.Force, req.KillTree)
	if err != nil {
		s.logger.Warn("Từ chối hoặc thất bại khi buộc dừng tiến trình", "ref", req.Ref, "err", err)
		status := http.StatusInternalServerError
		errStr := err.Error()
		if strings.Contains(errStr, "FORCE_CONFIRMATION_REQUIRED") {
			status = http.StatusBadRequest
		} else if strings.Contains(errStr, "PROTECTED_PROCESS_DENIED") {
			status = http.StatusForbidden
		} else if strings.Contains(errStr, "PID_RECYCLED") {
			status = http.StatusConflict
		} else if strings.Contains(errStr, "PROCESS_NOT_FOUND") {
			status = http.StatusNotFound
		}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error":  errStr,
			"status": "FAILED",
		})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(result)
}

func (s *Server) handleWorkspaces(w http.ResponseWriter, r *http.Request) {
	workspaces, err := s.dockerCtrl.ListWorkspaces(r.Context())
	if err != nil {
		s.logger.Error("Lỗi đọc danh sách workspaces", "err", err)
		http.Error(w, fmt.Sprintf(`{"error":"%s"}`, err.Error()), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"source":       "labops-go-agent",
		"collected_at": time.Now().UTC().Format(time.RFC3339),
		"count":        len(workspaces),
		"workspaces":   workspaces,
	})
}

// handleFullTelemetry trả về snapshot hợp nhất của Host, GPU và Docker trong 1 HTTP call
func (s *Server) handleFullTelemetry(w http.ResponseWriter, r *http.Request) {
	hostTel := s.hostCol.GetLatest()
	gpuTel := s.gpuCol.GetLatest()
	workspaces, _ := s.dockerCtrl.ListWorkspaces(r.Context())

	activeMembers := len(workspaces)
	hostTel.ActiveMembers = activeMembers

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"source":           "labops-go-agent",
		"collected_at":     time.Now().UTC().Format(time.RFC3339),
		"server":           hostTel,
		"gpus":             gpuTel,
		"workspaces":       workspaces,
		"workspaces_count": len(workspaces),
	})
}

// Start khởi chạy HTTP server
func (s *Server) Start() error {
	s.logger.Info("Khởi động LabOps Go Agent HTTP Server V2", "addr", s.httpSrv.Addr)
	return s.httpSrv.ListenAndServe()
}

// Shutdown dừng HTTP server an toàn
func (s *Server) Shutdown(ctx context.Context) error {
	return s.httpSrv.Shutdown(ctx)
}
