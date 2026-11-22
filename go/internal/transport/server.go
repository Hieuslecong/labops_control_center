package transport

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/Hieuslecong/labops_control_center/go/internal/agent"
	"github.com/Hieuslecong/labops_control_center/go/internal/config"
	"github.com/Hieuslecong/labops_control_center/go/internal/docker"
	"github.com/Hieuslecong/labops_control_center/go/internal/gateway"
	"github.com/Hieuslecong/labops_control_center/go/internal/gpu"
	"github.com/Hieuslecong/labops_control_center/go/internal/host"
	"github.com/Hieuslecong/labops_control_center/go/internal/process"
	"github.com/Hieuslecong/labops_control_center/go/internal/repositoryguard"
	"github.com/Hieuslecong/labops_control_center/go/internal/resources"
	"github.com/Hieuslecong/labops_control_center/go/internal/storage"
)

// Server quản lý HTTP API cục bộ của Node Agent
type Server struct {
	cfg        *config.AgentConfig
	hostCol    *host.Collector
	gpuCol     *gpu.Collector
	procCol    *process.Collector
	procCtrl   *process.Controller
	dockerCtrl *docker.Controller
	executor   *agent.CommandExecutor
	repoGuard  *repositoryguard.GuardService
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
	repoGuardSvc, err := repositoryguard.NewGuardService(cfg.RepoGuardDataDir)
	if err != nil {
		logger.Error("Không thể khởi tạo RepositoryGuard service", "err", err)
	}

	procCtrl := process.NewController(procCol)
	exec := agent.NewCommandExecutor(cfg, hostCol, gpuCol, procCol, procCtrl, dockerCtrl, gateway.NewReplayGuard(), logger)
	s := &Server{
		cfg:        cfg,
		hostCol:    hostCol,
		gpuCol:     gpuCol,
		procCol:    procCol,
		procCtrl:   procCtrl,
		dockerCtrl: dockerCtrl,
		executor:   exec,
		repoGuard:  repoGuardSvc,
		logger:     logger,
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/health", s.handleHealth)
	mux.HandleFunc("/api/v1/agent/execute", s.handleExecute)
	mux.HandleFunc("/v1/execute", s.handleExecute)
	mux.HandleFunc("/api/v1/agent/host", s.handleHostTelemetry)
	mux.HandleFunc("/api/v1/agent/gpus", s.handleGPUTelemetry)
	mux.HandleFunc("/api/v1/agent/processes", s.handleProcessTelemetry)
	mux.HandleFunc("/api/v1/agent/processes/tree", s.handleProcessTree)
	mux.HandleFunc("/api/v1/agent/processes/terminate", s.handleProcessTerminate)
	mux.HandleFunc("/api/v1/agent/processes/kill", s.handleProcessKill)
	mux.HandleFunc("/api/v1/agent/workspaces", s.handleWorkspaces)
	mux.HandleFunc("/api/v1/agent/telemetry/full", s.handleFullTelemetry)
	mux.HandleFunc("/api/v1/agent/storage", s.handleStorage)
	mux.HandleFunc("/api/v1/agent/storage/validate", s.handleStorageValidate)
	mux.HandleFunc("/api/v1/agent/resources/profile", s.handleResourceProfile)

	if repoGuardSvc != nil {
		repoHandler := repositoryguard.NewHTTPHandlerWithConfig(
			repoGuardSvc,
			cfg.BrokerVerifySecret,
			[]string{"/workspaces", "/mnt", "/var/lib/labops/workspaces", "/home"},
		)
		repoHandler.ResolveWorkspace = func(ctx context.Context, workspaceID, memberID, username, nodeID string) (repositoryguard.WorkspaceDelivery, error) {
			root, uid, gid, err := dockerCtrl.ResolveRepositoryWorkspace(ctx, workspaceID, memberID, username, nodeID)
			return repositoryguard.WorkspaceDelivery{Root: root, UID: uid, GID: gid}, err
		}
		repoHandler.RegisterRoutes(mux)
	}

	addr := fmt.Sprintf("%s:%d", cfg.BindAddr, cfg.Port)
	s.httpSrv = &http.Server{
		Addr:         addr,
		Handler:      s.authMiddleware(mux),
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 15 * time.Second,
	}

	return s
}

// authMiddleware xác thực token bảo mật nghiêm ngặt (AT-06)
func (s *Server) authMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/health" {
			next.ServeHTTP(w, r)
			return
		}

		// AT-06: Loại bỏ hoàn toàn cơ chế miễn trừ xác thực theo localhost
		token := r.Header.Get("X-LabOps-Agent-Secret")
		if token == "" {
			authHeader := r.Header.Get("Authorization")
			if strings.HasPrefix(strings.ToLower(authHeader), "bearer ") {
				token = strings.TrimSpace(authHeader[7:])
			}
		}

		// AT-06: Fail-closed — toàn bộ /api/v1/agent/* (cả GET lẫn POST) bắt buộc có SecretToken cấu hình và khớp (constant-time compare)
		expectedSecret := strings.TrimSpace(s.cfg.SecretToken)
		if expectedSecret == "" {
			writeJSONError(w, http.StatusServiceUnavailable, "Service unavailable: agent authentication secret is not configured")
			return
		}
		if token == "" || subtle.ConstantTimeCompare([]byte(token), []byte(expectedSecret)) != 1 {
			writeJSONError(w, http.StatusUnauthorized, "Unauthorized: invalid or missing agent token")
			return
		}

		next.ServeHTTP(w, r)
	})
}

func writeJSONError(w http.ResponseWriter, status int, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"error": msg,
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
		writeJSONError(w, http.StatusBadRequest, "Thiếu tham số 'ref'")
		return
	}

	tree, err := s.procCol.GetProcessTree(ref)
	if err != nil {
		s.logger.Warn("Lỗi tìm cây tiến trình", "ref", ref, "err", err)
		writeJSONError(w, http.StatusNotFound, err.Error())
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(tree)
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
		if strings.Contains(errStr, "PROTECTED_PROCESS_DENIED") || strings.Contains(errStr, "PERMISSION_DENIED") || strings.Contains(errStr, "HOST_MISMATCH") || strings.Contains(errStr, "CONTAINER_MISMATCH") {
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
		if strings.Contains(errStr, "FORCE_CONFIRMATION_REQUIRED") || strings.Contains(errStr, "INVALID_PROCESS_REF") {
			status = http.StatusBadRequest
		} else if strings.Contains(errStr, "PROTECTED_PROCESS_DENIED") || strings.Contains(errStr, "PERMISSION_DENIED") || strings.Contains(errStr, "HOST_MISMATCH") || strings.Contains(errStr, "CONTAINER_MISMATCH") {
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
		writeJSONError(w, http.StatusInternalServerError, err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
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

	var serverSnapshot *host.HostTelemetry
	if hostTel != nil {
		copyTel := *hostTel
		copyTel.ActiveMembers = len(workspaces)
		serverSnapshot = &copyTel
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"source":           "labops-go-agent",
		"collected_at":     time.Now().UTC().Format(time.RFC3339),
		"server":           serverSnapshot,
		"gpus":             gpuTel,
		"workspaces":       workspaces,
		"workspaces_count": len(workspaces),
	})
}

// handleStorage cung cấp danh sách ổ đĩa và cấp phát thư mục thành viên theo filesystem quota
func (s *Server) handleStorage(w http.ResponseWriter, r *http.Request) {
	// Production tuyệt đối không giả lập quota
	collector := storage.NewInventoryCollector(s.cfg.ServerID, storage.NewSystemQuotaManager(false))
	if r.Method == http.MethodGet {
		pools := collector.CollectStoragePools()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"source":        "labops-go-agent",
			"node_id":       s.cfg.ServerID,
			"storage_pools": pools,
			"count":         len(pools),
		})
		return
	}
	if r.Method == http.MethodPost {
		var req storage.MemberStorageRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSONError(w, http.StatusBadRequest, "Invalid JSON payload: "+err.Error())
			return
		}
		if req.HostID == "" {
			req.HostID = s.cfg.ServerID
		}
		provisioner := storage.NewProvisioner(s.cfg.ServerID, collector, storage.NewSystemQuotaManager(false))
		res, err := provisioner.ProvisionMemberStorage(req)
		if err != nil {
			s.logger.Warn("Lỗi cấp phát lưu trữ cho member", "err", err, "req", req)
			status := http.StatusInternalServerError
			if strings.Contains(err.Error(), "ADMISSION_REJECTED") || strings.Contains(err.Error(), "SECURITY_VIOLATION") || strings.Contains(err.Error(), "không hợp lệ") {
				status = http.StatusBadRequest
			}
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(status)
			_ = json.NewEncoder(w).Encode(map[string]any{
				"error":  err.Error(),
				"status": "FAILED",
			})
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(res)
		return
	}
	http.Error(w, `{"error":"Method not allowed"}`, http.StatusMethodNotAllowed)
}

// handleStorageValidate kiểm tra tính hợp lệ và dung lượng khả dụng của pool trước khi cấp phát
func (s *Server) handleStorageValidate(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, `{"error":"Method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}
	var req struct {
		StorageID string `json:"storage_id"`
		QuotaGiB  int64  `json:"quota_gib"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSONError(w, http.StatusBadRequest, "Invalid JSON payload: "+err.Error())
		return
	}
	collector := storage.NewInventoryCollector(s.cfg.ServerID, storage.NewSystemQuotaManager(false))
	provisioner := storage.NewProvisioner(s.cfg.ServerID, collector, storage.NewSystemQuotaManager(false))
	pool, err := provisioner.ValidateAllocation(req.StorageID, req.QuotaGiB)
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"valid": false,
			"error": err.Error(),
		})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"valid":           true,
		"storage_id":      req.StorageID,
		"quota_gib":       req.QuotaGiB,
		"allocatable_gib": pool.AllocatableGiB,
		"mount_point":     pool.MountPoint,
	})
}

// handleResourceProfile trả về thông tin Host Resource Profile và áp lực bộ nhớ (PSI)
func (s *Server) handleResourceProfile(w http.ResponseWriter, r *http.Request) {
	memMgr := resources.NewMemoryPolicyManager(s.cfg.ServerID, 20.0)
	prof := memMgr.EvaluateHostProfile()
	psi := memMgr.ReadMemoryPressure()
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"source":          "labops-go-agent",
		"node_id":         s.cfg.ServerID,
		"profile":         prof,
		"memory_pressure": psi,
	})
}

// handleExecute xử lý thống nhất mọi hành động allowlisted qua HTTP authenticated từ Control Plane.
func (s *Server) handleExecute(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, `{"error":"Method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}
	var payload struct {
		CommandID            string         `json:"command_id"`
		RequestID            string         `json:"request_id"`
		NodeID               string         `json:"node_id"`
		ActorID              string         `json:"actor_id"`
		Action               string         `json:"action"`
		Params               map[string]any `json:"params"`
		Parameters           map[string]any `json:"parameters"`
		AuthorizationContext string         `json:"authorization_context"`
		IdempotencyKey       string         `json:"idempotency_key"`
	}
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		writeJSONError(w, http.StatusBadRequest, "Invalid JSON payload: "+err.Error())
		return
	}
	if payload.RequestID == "" {
		payload.RequestID = strings.TrimSpace(r.Header.Get("X-Request-Id"))
	}
	if payload.IdempotencyKey == "" {
		payload.IdempotencyKey = strings.TrimSpace(r.Header.Get("Idempotency-Key"))
	}
	rawParams := payload.Parameters
	if len(rawParams) == 0 {
		rawParams = payload.Params
	}
	strParams := make(map[string]string, len(rawParams))
	for k, v := range rawParams {
		if v == nil {
			continue
		}
		switch val := v.(type) {
		case string:
			strParams[k] = val
		default:
			if b, err := json.Marshal(val); err == nil {
				strParams[k] = string(b)
			}
		}
	}

	execRes := s.executor.Execute(r.Context(), &agent.ExecuteCommandRequest{
		CommandID:            payload.CommandID,
		RequestID:            payload.RequestID,
		NodeID:               payload.NodeID,
		ActorID:              payload.ActorID,
		Action:               payload.Action,
		Parameters:           strParams,
		AuthorizationContext: payload.AuthorizationContext,
		IdempotencyKey:       payload.IdempotencyKey,
	})

	status := http.StatusOK
	if !execRes.Success {
		switch execRes.ErrorCode {
		case "NODE_MISMATCH", "DELEGATION_INVALID", "ACTION_UNAUTHORIZED", "REPLAY_DETECTED", "UNSUPPORTED_ACTION":
			status = http.StatusForbidden
		case "IDEMPOTENCY_CONFLICT", "INVALID_PROCESS_REF", "INVALID_PROVISION_SPEC", "INVALID_CONTAINER_ID", "INVALID_CANONICAL_WORKSPACE", "INVALID_KEYS_JSON", "INVALID_STORAGE_SPEC":
			status = http.StatusBadRequest
		case "COMMAND_EXPIRED":
			status = http.StatusRequestTimeout
		default:
			status = http.StatusConflict
		}
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(execRes)
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
