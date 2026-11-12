package gateway

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/Hieuslecong/labops_control_center/go/internal/pki"
)

// HTTPControlPlaneServer phục vụ API cho FastAPI Backend trên loopback
type HTTPControlPlaneServer struct {
	sessionManager *SessionManager
	pkiCA          *pki.CACertificate
	tokenManager   *pki.EnrollmentTokenManager
	revocationMgr  *pki.RevocationManager
	controlSecret  string
	server         *http.Server
	logger         *slog.Logger
	port           int
	bindAddr       string
}

func NewHTTPControlPlaneServer(
	bindAddr string,
	port int,
	sm *SessionManager,
	ca *pki.CACertificate,
	tm *pki.EnrollmentTokenManager,
	rm *pki.RevocationManager,
	controlSecret string,
	logger *slog.Logger,
) *HTTPControlPlaneServer {
	if logger == nil {
		logger = slog.Default()
	}

	h := &HTTPControlPlaneServer{
		sessionManager: sm,
		pkiCA:          ca,
		tokenManager:   tm,
		revocationMgr:  rm,
		controlSecret:  controlSecret,
		logger:         logger,
		port:           port,
		bindAddr:       bindAddr,
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/health", h.handleHealth)
	mux.HandleFunc("/api/v1/gateway/nodes", h.handleNodes)
	mux.HandleFunc("/api/v1/gateway/nodes/", h.handleNodeDetail)
	mux.HandleFunc("/api/v1/gateway/dispatch", h.handleDispatch)
	mux.HandleFunc("/api/v1/gateway/enroll", h.handleEnrollToken)
	mux.HandleFunc("/api/v1/gateway/revoke", h.handleRevoke)
	mux.HandleFunc("/api/v1/gateway/issue-cert", h.handleIssueCert)

	addr := fmt.Sprintf("%s:%d", bindAddr, port)
	h.server = &http.Server{
		Addr:         addr,
		Handler:      mux,
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 60 * time.Second,
	}

	return h
}

// verifyControlAuth kiểm tra token xác thực của Control Plane API
func (h *HTTPControlPlaneServer) verifyControlAuth(r *http.Request) bool {
	if h.controlSecret == "" {
		// Nếu chưa cấu hình secret xác thực, từ chối mọi yêu cầu đặc quyền (fail-closed)
		return false
	}
	authHdr := r.Header.Get("Authorization")
	token := ""
	if strings.HasPrefix(strings.ToLower(authHdr), "bearer ") {
		token = strings.TrimSpace(authHdr[7:])
	}
	if token == "" {
		token = r.Header.Get("X-Gateway-Control-Secret")
	}
	if token == "" {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(token), []byte(h.controlSecret)) == 1
}

func (h *HTTPControlPlaneServer) Start() error {
	h.logger.Info("🌐 LabOps Gateway Control Plane HTTP API đang lắng nghe", "address", h.server.Addr)
	return h.server.ListenAndServe()
}

func (h *HTTPControlPlaneServer) Stop(ctx context.Context) error {
	return h.server.Shutdown(ctx)
}

func (h *HTTPControlPlaneServer) handleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"status":    "UP",
		"service":   "labops-agent-gateway",
		"timestamp": time.Now().Unix(),
	})
}

func (h *HTTPControlPlaneServer) handleNodes(w http.ResponseWriter, r *http.Request) {
	if !h.verifyControlAuth(r) {
		http.Error(w, "Yêu cầu xác thực Gateway Control Plane (HTTP 401)", http.StatusUnauthorized)
		return
	}
	if r.Method != http.MethodGet {
		http.Error(w, "Chỉ hỗ trợ GET", http.StatusMethodNotAllowed)
		return
	}
	nodes := h.sessionManager.ListNodes()
	writeJSON(w, http.StatusOK, map[string]any{
		"nodes":       nodes,
		"total_count": len(nodes),
	})
}

func (h *HTTPControlPlaneServer) handleNodeDetail(w http.ResponseWriter, r *http.Request) {
	if !h.verifyControlAuth(r) {
		http.Error(w, "Yêu cầu xác thực Gateway Control Plane (HTTP 401)", http.StatusUnauthorized)
		return
	}
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/gateway/nodes/")
	parts := strings.Split(path, "/")
	if len(parts) == 0 || parts[0] == "" {
		http.Error(w, "Thiếu node_id", http.StatusBadRequest)
		return
	}

	nodeID := parts[0]

	// Kiểm tra nếu là route telemetry: /api/v1/gateway/nodes/{id}/telemetry
	if len(parts) >= 2 && parts[1] == "telemetry" {
		snapshot, err := h.sessionManager.GetTelemetrySnapshot(nodeID)
		if err != nil {
			http.Error(w, err.Error(), http.StatusNotFound)
			return
		}
		writeJSON(w, http.StatusOK, snapshot)
		return
	}

	sess, ok := h.sessionManager.GetSession(nodeID)
	if !ok {
		http.Error(w, fmt.Sprintf("Node '%s' không tồn tại", nodeID), http.StatusNotFound)
		return
	}

	summary := &NodeSummary{
		NodeID:          sess.NodeID,
		Hostname:        sess.Hostname,
		Role:            sess.Role,
		Status:          sess.Status,
		AgentVersion:    sess.AgentVersion,
		ManagementIP:    sess.ManagementIP,
		GPUCount:        sess.GPUCount,
		ConnectedAt:     sess.ConnectedAt,
		LastHeartbeat:   sess.LastHeartbeat,
		DegradedReasons: sess.DegradedReasons,
	}
	writeJSON(w, http.StatusOK, summary)
}

func (h *HTTPControlPlaneServer) handleDispatch(w http.ResponseWriter, r *http.Request) {
	if !h.verifyControlAuth(r) {
		http.Error(w, "Yêu cầu xác thực Gateway Control Plane (HTTP 401)", http.StatusUnauthorized)
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "Chỉ hỗ trợ POST", http.StatusMethodNotAllowed)
		return
	}

	var req DispatchRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, fmt.Sprintf("Lỗi parse request body: %v", err), http.StatusBadRequest)
		return
	}

	if req.NodeID == "" || req.Action == "" {
		http.Error(w, "Thiếu node_id hoặc action", http.StatusBadRequest)
		return
	}

	resp, err := h.sessionManager.DispatchCommand(r.Context(), &req)
	if err != nil {
		h.logger.Error("Lỗi dispatch command", "node_id", req.NodeID, "action", req.Action, "err", err)
	}

	writeJSON(w, http.StatusOK, resp)
}

type EnrollRequest struct {
	NodeID     string `json:"node_id"`
	TTLMinutes int    `json:"ttl_minutes"`
}

func (h *HTTPControlPlaneServer) handleEnrollToken(w http.ResponseWriter, r *http.Request) {
	if !h.verifyControlAuth(r) {
		http.Error(w, "Yêu cầu xác thực Gateway Control Plane (HTTP 401)", http.StatusUnauthorized)
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "Chỉ hỗ trợ POST", http.StatusMethodNotAllowed)
		return
	}

	var req EnrollRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Lỗi cú pháp JSON", http.StatusBadRequest)
		return
	}
	if req.NodeID == "" {
		http.Error(w, "Thiếu node_id", http.StatusBadRequest)
		return
	}
	if req.TTLMinutes <= 0 {
		req.TTLMinutes = 15
	}

	token := h.tokenManager.GenerateToken(req.NodeID, time.Duration(req.TTLMinutes)*time.Minute)
	writeJSON(w, http.StatusOK, map[string]any{
		"node_id":    req.NodeID,
		"token":      token,
		"expires_in": req.TTLMinutes * 60,
	})
}

type RevokeRequest struct {
	NodeID string `json:"node_id"`
	Reason string `json:"reason"`
}

func (h *HTTPControlPlaneServer) handleRevoke(w http.ResponseWriter, r *http.Request) {
	if !h.verifyControlAuth(r) {
		http.Error(w, "Yêu cầu xác thực Gateway Control Plane (HTTP 401)", http.StatusUnauthorized)
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "Chỉ hỗ trợ POST", http.StatusMethodNotAllowed)
		return
	}

	var req RevokeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Lỗi cú pháp JSON", http.StatusBadRequest)
		return
	}
	if req.NodeID == "" {
		http.Error(w, "Thiếu node_id", http.StatusBadRequest)
		return
	}
	if req.Reason == "" {
		req.Reason = "Thu hồi quyền truy cập bởi Admin"
	}

	h.revocationMgr.RevokeNode(req.NodeID, req.Reason)
	h.sessionManager.RemoveSession(req.NodeID)

	h.logger.Warn("Đã thu hồi chứng chỉ và ngắt phiên của node", "node_id", req.NodeID, "reason", req.Reason)
	writeJSON(w, http.StatusOK, map[string]any{
		"success": true,
		"node_id": req.NodeID,
		"message": fmt.Sprintf("Node '%s' đã bị thu hồi", req.NodeID),
	})
}

type IssueCertRequest struct {
	NodeID          string `json:"node_id"`
	EnrollmentToken string `json:"enrollment_token"`
}

func (h *HTTPControlPlaneServer) handleIssueCert(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Chỉ hỗ trợ POST", http.StatusMethodNotAllowed)
		return
	}

	var req IssueCertRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Lỗi cú pháp JSON", http.StatusBadRequest)
		return
	}

	if req.NodeID == "" || req.EnrollmentToken == "" {
		http.Error(w, "Thiếu node_id hoặc enrollment_token", http.StatusBadRequest)
		return
	}

	// Xác minh token gắn chặt với đúng NodeID và chỉ sử dụng một lần duy nhất
	if !h.tokenManager.ConsumeTokenForNode(req.EnrollmentToken, req.NodeID) {
		h.logger.Warn("Từ chối cấp chứng chỉ: Token không hợp lệ, hết hạn hoặc sai node_id",
			"claimed_node", req.NodeID)
		http.Error(w, "Token ghi danh (enrollment token) không hợp lệ, đã hết hạn hoặc không khớp với node_id", http.StatusUnauthorized)
		return
	}

	bundle, err := pki.IssueNodeCert(h.pkiCA, req.NodeID)
	if err != nil {
		h.logger.Error("Lỗi phát hành chứng chỉ cho node", "node_id", req.NodeID, "err", err)
		http.Error(w, fmt.Sprintf("Lỗi cấp chứng chỉ: %v", err), http.StatusInternalServerError)
		return
	}

	h.revocationMgr.AllowNode(req.NodeID)

	writeJSON(w, http.StatusOK, map[string]any{
		"node_id":         req.NodeID,
		"ca_certificate":  string(h.pkiCA.CertPEM),
		"certificate":     string(bundle.CertPEM),
		"private_key":     string(bundle.KeyPEM),
		"issued_at_unix":  time.Now().Unix(),
	})
}

func writeJSON(w http.ResponseWriter, status int, data any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(data)
}

