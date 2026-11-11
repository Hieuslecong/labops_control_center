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
		dockerCtrl: dockerCtrl,
		logger:     logger,
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/health", s.handleHealth)
	mux.HandleFunc("/api/v1/agent/host", s.handleHostTelemetry)
	mux.HandleFunc("/api/v1/agent/gpus", s.handleGPUTelemetry)
	mux.HandleFunc("/api/v1/agent/processes", s.handleProcessTelemetry)
	mux.HandleFunc("/api/v1/agent/workspaces", s.handleWorkspaces)
	mux.HandleFunc("/api/v1/agent/telemetry/full", s.handleFullTelemetry)

	addr := fmt.Sprintf("%s:%d", cfg.BindAddr, cfg.Port)
	s.httpSrv = &http.Server{
		Addr:         addr,
		Handler:      s.authMiddleware(mux),
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 10 * time.Second,
	}

	return s
}

// authMiddleware xác thực token nếu có cấu hình
func (s *Server) authMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Health check luôn mở cho liveness probe
		if r.URL.Path == "/health" {
			next.ServeHTTP(w, r)
			return
		}

		// Nếu gọi từ localhost, cho phép hoặc kiểm tra header
		clientIP := r.RemoteAddr
		if strings.HasPrefix(clientIP, "127.0.0.1") || strings.HasPrefix(clientIP, "[::1]") {
			// Local trusted access
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
		"status":      "HEALTHY",
		"server_id":   s.cfg.ServerID,
		"hostname":    s.cfg.Hostname,
		"time":        time.Now().UTC().Format(time.RFC3339),
		"agent":       "labops-go-agent-v1",
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

	// Đếm số thành viên active
	activeMembers := len(workspaces)
	hostTel.ActiveMembers = activeMembers

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"source":          "labops-go-agent",
		"collected_at":    time.Now().UTC().Format(time.RFC3339),
		"server":          hostTel,
		"gpus":            gpuTel,
		"workspaces":      workspaces,
		"workspaces_count": len(workspaces),
	})
}

// Start khởi chạy HTTP server
func (s *Server) Start() error {
	s.logger.Info("Khởi động LabOps Go Agent HTTP Server", "addr", s.httpSrv.Addr)
	return s.httpSrv.ListenAndServe()
}

// Shutdown dừng HTTP server an toàn
func (s *Server) Shutdown(ctx context.Context) error {
	return s.httpSrv.Shutdown(ctx)
}

