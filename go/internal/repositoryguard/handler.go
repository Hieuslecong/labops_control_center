package repositoryguard

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"
)

const brokerReplayWindowSeconds int64 = 300

// HTTPHandler serves Repository Guard API endpoints with broker HMAC & strict RBAC enforcement.
type HTTPHandler struct {
	service      *GuardService
	brokerSecret string
}

// NewHTTPHandler constructs a handler without broker HMAC secret (intended for unit tests only; still enforces header identity & RBAC).
func NewHTTPHandler(service *GuardService) *HTTPHandler {
	return &HTTPHandler{service: service}
}

// NewHTTPHandlerWithConfig constructs a production-hardened handler enforcing broker HMAC signatures and workspace roots.
func NewHTTPHandlerWithConfig(service *GuardService, brokerSecret string, allowedRoots []string) *HTTPHandler {
	if service != nil && len(allowedRoots) > 0 {
		service.SetWorkspaceRoots(allowedRoots)
	}
	return &HTTPHandler{
		service:      service,
		brokerSecret: brokerSecret,
	}
}

// CanonicalBrokerSigningPayload builds the canonical string signed by the backend/broker.
func CanonicalBrokerSigningPayload(ts int64, actor, role, method, path string) string {
	return fmt.Sprintf("%d:%s:%s:%s:%s", ts, strings.TrimSpace(actor), strings.ToUpper(strings.TrimSpace(role)), strings.ToUpper(strings.TrimSpace(method)), path)
}

// SignBrokerRequest computes hex HMAC-SHA256 signature for trusted broker forwarding.
func SignBrokerRequest(secret, actor, role, method, path string, ts int64) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(CanonicalBrokerSigningPayload(ts, actor, role, method, path)))
	return hex.EncodeToString(mac.Sum(nil))
}

// ApplyBrokerAuthHeaders sets verified identity and HMAC signature headers on an outgoing HTTP request.
func ApplyBrokerAuthHeaders(req *http.Request, secret, actor, role string, ts int64) {
	req.Header.Set("X-LabOps-Verified-Actor", actor)
	req.Header.Set("X-LabOps-Verified-Role", strings.ToUpper(role))
	if secret != "" {
		req.Header.Set("X-LabOps-Verified-Timestamp", strconv.FormatInt(ts, 10))
		req.Header.Set("X-LabOps-Verified-Signature", SignBrokerRequest(secret, actor, role, req.Method, req.URL.Path, ts))
	}
}

func (h *HTTPHandler) RegisterRoutes(mux *http.ServeMux) {
	mux.HandleFunc("/api/v1/agent/repositoryguard/requests", h.handleRequests)
	mux.HandleFunc("/api/v1/agent/repositoryguard/requests/", h.handleRequestByID)
	mux.HandleFunc("/api/v1/agent/repositoryguard/deliver", h.handleDeliver)
	mux.HandleFunc("/api/v1/agent/repositoryguard/audit", h.handleAudit)
}

func writeJSON(w http.ResponseWriter, code int, payload interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(payload)
}

func writeError(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}

func (h *HTTPHandler) authenticateRequest(r *http.Request) (actor string, isAdmin bool, errCode int, errMsg string) {
	actor = strings.TrimSpace(r.Header.Get("X-LabOps-Verified-Actor"))
	role := strings.ToUpper(strings.TrimSpace(r.Header.Get("X-LabOps-Verified-Role")))

	if h.brokerSecret != "" {
		tsRaw := strings.TrimSpace(r.Header.Get("X-LabOps-Verified-Timestamp"))
		sigRaw := strings.ToLower(strings.TrimSpace(r.Header.Get("X-LabOps-Verified-Signature")))
		if actor == "" || role == "" || tsRaw == "" || sigRaw == "" {
			return "", false, http.StatusUnauthorized, "Unauthorized: thiếu tiêu đề xác thực broker"
		}
		if role != "ADMIN" && role != "RESEARCHER" {
			return "", false, http.StatusForbidden, "Forbidden: vai trò không hợp lệ"
		}

		ts, err := strconv.ParseInt(tsRaw, 10, 64)
		if err != nil {
			return "", false, http.StatusUnauthorized, "Unauthorized: timestamp chữ ký không hợp lệ"
		}
		diff := time.Now().Unix() - ts
		if diff < -brokerReplayWindowSeconds || diff > brokerReplayWindowSeconds {
			return "", false, http.StatusUnauthorized, "Unauthorized: chữ ký broker đã hết hạn"
		}

		expectedSig := SignBrokerRequest(h.brokerSecret, actor, role, r.Method, r.URL.Path, ts)
		providedBytes, decErr := hex.DecodeString(sigRaw)
		expectedBytes, _ := hex.DecodeString(expectedSig)
		if decErr != nil || !hmac.Equal(providedBytes, expectedBytes) {
			return "", false, http.StatusUnauthorized, "Unauthorized: chữ ký broker không hợp lệ"
		}

		return actor, role == "ADMIN", 0, ""
	}

	if actor == "" {
		return "", false, http.StatusUnauthorized, "Unauthorized: thiếu danh tính X-LabOps-Verified-Actor"
	}
	// Never trust query params like ?is_admin=true; only verified header role grants ADMIN.
	return actor, role == "ADMIN", 0, ""
}

func (h *HTTPHandler) handleRequests(w http.ResponseWriter, r *http.Request) {
	actor, isAdmin, errCode, errMsg := h.authenticateRequest(r)
	if errCode != 0 {
		writeError(w, errCode, errMsg)
		return
	}

	switch r.Method {
	case http.MethodGet:
		var list []*RepositoryRequest
		if isAdmin {
			filterSubmitter := strings.TrimSpace(r.URL.Query().Get("submitter"))
			if filterSubmitter != "" {
				list = h.service.ListRequests(filterSubmitter, false)
			} else {
				list = h.service.ListRequests("", true)
			}
		} else {
			list = h.service.ListRequests(actor, false)
		}
		if list == nil {
			list = []*RepositoryRequest{}
		}
		writeJSON(w, http.StatusOK, list)

	case http.MethodPost:
		var payload struct {
			RepoURL         string `json:"repo_url"`
			Ref             string `json:"ref"`
			Submitter       string `json:"submitter"`
			TargetWorkspace string `json:"target_workspace"`
		}
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			writeError(w, http.StatusBadRequest, "Dữ liệu JSON không hợp lệ")
			return
		}

		claimedSubmitter := strings.TrimSpace(payload.Submitter)
		if !isAdmin && claimedSubmitter != "" && claimedSubmitter != actor {
			writeError(w, http.StatusForbidden, "Forbidden: không được phép giả mạo người gửi yêu cầu")
			return
		}
		submitter := actor
		if isAdmin && claimedSubmitter != "" {
			submitter = claimedSubmitter
		}

		req, err := h.service.SubmitRequest(r.Context(), payload.RepoURL, payload.Ref, submitter, payload.TargetWorkspace)
		if err != nil {
			writeError(w, http.StatusBadRequest, err.Error())
			return
		}

		writeJSON(w, http.StatusAccepted, req)

	default:
		writeError(w, http.StatusMethodNotAllowed, "Method not allowed")
	}
}

func (h *HTTPHandler) handleRequestByID(w http.ResponseWriter, r *http.Request) {
	actor, isAdmin, errCode, errMsg := h.authenticateRequest(r)
	if errCode != 0 {
		writeError(w, errCode, errMsg)
		return
	}

	path := strings.TrimPrefix(r.URL.Path, "/api/v1/agent/repositoryguard/requests/")
	parts := strings.Split(path, "/")
	reqID := strings.TrimSpace(parts[0])
	if reqID == "" {
		writeError(w, http.StatusBadRequest, "Thiếu request ID")
		return
	}

	if len(parts) > 1 && parts[1] == "decision" && r.Method == http.MethodPost {
		if !isAdmin {
			writeError(w, http.StatusForbidden, "Forbidden: chỉ Admin mới có quyền phê duyệt hoặc từ chối kho mã")
			return
		}

		var payload struct {
			Decision string `json:"decision"`
			Reviewer string `json:"reviewer"`
			Reason   string `json:"reason"`
		}
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			writeError(w, http.StatusBadRequest, "Dữ liệu quyết định không hợp lệ")
			return
		}

		updated, err := h.service.AdminDecision(reqID, payload.Decision, actor, payload.Reason)
		if err != nil {
			writeError(w, http.StatusBadRequest, err.Error())
			return
		}

		writeJSON(w, http.StatusOK, updated)
		return
	}

	if r.Method == http.MethodGet {
		req, found := h.service.GetRequest(reqID)
		if !found {
			writeError(w, http.StatusNotFound, "Không tìm thấy yêu cầu")
			return
		}
		if !isAdmin && req.Submitter != actor {
			writeError(w, http.StatusForbidden, "Forbidden: bạn không có quyền xem yêu cầu của thành viên khác")
			return
		}
		writeJSON(w, http.StatusOK, req)
		return
	}

	writeError(w, http.StatusMethodNotAllowed, "Method not allowed")
}

func (h *HTTPHandler) handleDeliver(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	actor, isAdmin, errCode, errMsg := h.authenticateRequest(r)
	if errCode != 0 {
		writeError(w, errCode, errMsg)
		return
	}

	var payload DeliverPayload
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		writeError(w, http.StatusBadRequest, "Dữ liệu phân phối không hợp lệ")
		return
	}

	if !isAdmin && strings.TrimSpace(payload.DeliverAsUser) != "" && payload.DeliverAsUser != actor {
		writeError(w, http.StatusForbidden, "Forbidden: không thể phân phối dưới danh nghĩa thành viên khác")
		return
	}
	if strings.TrimSpace(payload.RequestID) == "" || strings.TrimSpace(payload.TargetDirectory) == "" {
		writeError(w, http.StatusBadRequest, "Thiếu request_id hoặc target_directory")
		return
	}

	if err := h.service.DeliverAuthorized(payload.RequestID, actor, isAdmin, payload.TargetDirectory); err != nil {
		if errors.Is(err, ErrPermissionDenied) {
			writeError(w, http.StatusForbidden, err.Error())
			return
		}
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"success":    true,
		"request_id": payload.RequestID,
		"dest_dir":   payload.TargetDirectory,
		"message":    "Phân phối mã nguồn đã xác minh thành công vào workspace",
	})
}

func (h *HTTPHandler) handleAudit(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	_, isAdmin, errCode, errMsg := h.authenticateRequest(r)
	if errCode != 0 {
		writeError(w, errCode, errMsg)
		return
	}
	if !isAdmin {
		writeError(w, http.StatusForbidden, "Forbidden: chỉ Admin mới có quyền truy cập nhật ký kiểm toán")
		return
	}

	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		if parsed, err := strconv.Atoi(l); err == nil && parsed > 0 && parsed <= 500 {
			limit = parsed
		}
	}

	events := h.service.GetAuditEvents(limit)
	writeJSON(w, http.StatusOK, events)
}
