package repositoryguard

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

type HTTPHandler struct {
	service      *GuardService
	resolveActor RepositoryActorResolver
}

// RepositoryActorResolver must be backed by trusted authentication context. It must
// never derive identity or role from query parameters or request payload fields.
type RepositoryActor struct {
	ID          string
	Role        string
	MemberID    string
	WorkspaceID string
	NodeID      string
	ResourceID  string
	Scopes      []string
}

type RepositoryActorResolver func(r *http.Request, action, resource string) (RepositoryActor, error)

// NewScopedTokenResolver creates an actor resolver for signed Backend-issued
// capabilities. It never reads user identity or role from request data.
func NewScopedTokenResolver(secret, expectedNodeID string) RepositoryActorResolver {
	return func(r *http.Request, action, resource string) (RepositoryActor, error) {
		if len(secret) < 32 || expectedNodeID == "" {
			return RepositoryActor{}, errors.New("repository authorization is not configured")
		}
		header := strings.TrimSpace(r.Header.Get("Authorization"))
		if len(header) < 8 || !strings.EqualFold(header[:7], "Bearer ") {
			return RepositoryActor{}, errors.New("repository capability is missing")
		}
		claims, err := VerifyScopedToken(strings.TrimSpace(header[7:]), secret, time.Now())
		if err != nil {
			return RepositoryActor{}, err
		}
		if claims.NodeID != expectedNodeID {
			return RepositoryActor{}, errors.New("repository capability targets a different node")
		}
		scope, ok := map[string]string{
			"repository.request.submit": "repo:request:submit",
			"repository.requests.list":  "repo:request:list",
			"repository.request.read":   "repo:request:read",
			"repository.artifact.read":  "repo:bundle:read",
			"repository.request.decide": "repo:request:decide",
			"repository.audit.read":     "repo:audit:read",
		}[action]
		if !ok || !hasRepositoryScope(claims.Scopes, scope) {
			return RepositoryActor{}, errors.New("repository capability does not authorize this action")
		}
		if claims.ResourceID != "" && claims.ResourceID != resource {
			return RepositoryActor{}, errors.New("repository capability targets a different resource")
		}
		if action == "repository.request.submit" && claims.Role != "ADMIN" && claims.WorkspaceID != resource {
			return RepositoryActor{}, errors.New("repository capability targets a different workspace")
		}
		return RepositoryActor{
			ID: claims.Subject, Role: claims.Role, MemberID: claims.MemberID,
			WorkspaceID: claims.WorkspaceID, NodeID: claims.NodeID,
			ResourceID: claims.ResourceID, Scopes: append([]string(nil), claims.Scopes...),
		}, nil
	}
}

func hasRepositoryScope(scopes []string, expected string) bool {
	for _, scope := range scopes {
		if scope == expected {
			return true
		}
	}
	return false
}

func NewHTTPHandler(service *GuardService, resolvers ...RepositoryActorResolver) *HTTPHandler {
	h := &HTTPHandler{service: service}
	if len(resolvers) == 1 {
		h.resolveActor = resolvers[0]
	}
	return h
}

func (h *HTTPHandler) actor(w http.ResponseWriter, r *http.Request, action, resource string) (RepositoryActor, bool) {
	if h.resolveActor == nil {
		writeJSONError(w, http.StatusServiceUnavailable, "trusted repository actor authorization unavailable")
		return RepositoryActor{}, false
	}
	actor, err := h.resolveActor(r, action, resource)
	if err != nil {
		writeJSONError(w, http.StatusUnauthorized, "repository actor authentication failed")
		return RepositoryActor{}, false
	}
	if strings.TrimSpace(actor.ID) == "" || (actor.Role != "ADMIN" && actor.Role != "RESEARCHER") {
		writeJSONError(w, http.StatusForbidden, "repository actor is not authorized")
		return RepositoryActor{}, false
	}
	return actor, true
}

func writeJSONError(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"success": false, "error": message})
}

func requireRepositoryAdmin(w http.ResponseWriter, actor RepositoryActor) bool {
	if actor.Role == "ADMIN" {
		return true
	}
	writeJSONError(w, http.StatusForbidden, "admin role required")
	return false
}

func (h *HTTPHandler) RegisterRoutes(mux *http.ServeMux) {
	mux.HandleFunc("/api/v1/agent/repositoryguard/requests", h.handleRequests)
	mux.HandleFunc("/api/v1/agent/repositoryguard/requests/", h.handleRequestByID)
	mux.HandleFunc("/api/v1/agent/repositoryguard/deliver", h.handleDeliver)
	mux.HandleFunc("/api/v1/agent/repositoryguard/audit", h.handleAudit)
}

func (h *HTTPHandler) handleRequests(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		actor, ok := h.actor(w, r, "repository.requests.list", "")
		if !ok {
			return
		}
		list := h.service.ListRequestsForWorkspace(actor.ID, actor.Role == "ADMIN", actor.WorkspaceID)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(list)

	case http.MethodPost:
		r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
		var payload struct {
			RepoURL         string `json:"repo_url"`
			Ref             string `json:"ref"`
			TargetWorkspace string `json:"target_workspace"`
			IdempotencyKey  string `json:"idempotency_key"`
		}
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			http.Error(w, `{"error":"Dữ liệu JSON không hợp lệ"}`, http.StatusBadRequest)
			return
		}

		workspaceID := strings.TrimSpace(payload.TargetWorkspace)
		if strings.TrimSpace(workspaceID) == "" {
			writeJSONError(w, http.StatusBadRequest, "target workspace is required")
			return
		}
		actor, ok := h.actor(w, r, "repository.request.submit", workspaceID)
		if !ok {
			return
		}
		if actor.Role != "ADMIN" && actor.WorkspaceID != workspaceID {
			writeJSONError(w, http.StatusForbidden, "workspace is outside the authenticated capability")
			return
		}

		req, err := h.service.SubmitRequestWithKey(r.Context(), payload.RepoURL, payload.Ref, actor.ID, workspaceID, payload.IdempotencyKey)
		if err != nil {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		_ = json.NewEncoder(w).Encode(req)

	default:
		http.Error(w, `{"error":"Method not allowed"}`, http.StatusMethodNotAllowed)
	}
}

func (h *HTTPHandler) handleRequestByID(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/agent/repositoryguard/requests/")
	parts := strings.Split(path, "/")
	reqID := parts[0]

	if reqID == "" {
		http.Error(w, `{"error":"Thiếu request ID"}`, http.StatusBadRequest)
		return
	}

	if len(parts) > 1 && parts[1] == "bundle" && r.Method == http.MethodGet {
		actor, ok := h.actor(w, r, "repository.artifact.read", reqID)
		if !ok {
			return
		}
		req, found := h.service.GetRequest(reqID)
		if !found || (actor.Role != "ADMIN" && (req.Submitter != actor.ID || req.TargetWorkspace != actor.WorkspaceID)) {
			writeJSONError(w, http.StatusNotFound, "approved repository artifact not found")
			return
		}
		bundle, digest, commit, err := h.service.OpenApprovedBundle(reqID)
		if err != nil {
			writeJSONError(w, http.StatusConflict, "approved Git bundle is unavailable or invalid")
			return
		}
		defer bundle.Close()
		w.Header().Set("Content-Type", "application/vnd.git-bundle")
		w.Header().Set("X-LabOps-Artifact-SHA256", digest)
		w.Header().Set("X-LabOps-Commit-SHA", commit)
		w.Header().Set("Cache-Control", "no-store")
		if info, statErr := bundle.Stat(); statErr == nil {
			w.Header().Set("Content-Length", strconv.FormatInt(info.Size(), 10))
		}
		if _, err := io.Copy(w, bundle); err != nil {
			h.service.recordDeliveryReadFailure(reqID, actor.ID, err)
		}
		return
	}

	if len(parts) > 1 && parts[1] == "decision" && r.Method == http.MethodPost {
		var payload struct {
			Decision string `json:"decision"`
			Reason   string `json:"reason"`
		}
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			http.Error(w, `{"error":"Dữ liệu quyết định không hợp lệ"}`, http.StatusBadRequest)
			return
		}
		actor, ok := h.actor(w, r, "repository.request.decide", reqID)
		if !ok {
			return
		}
		if !requireRepositoryAdmin(w, actor) {
			return
		}

		updated, err := h.service.AdminDecision(reqID, payload.Decision, actor.ID, payload.Reason)
		if err != nil {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(updated)
		return
	}

	if r.Method == http.MethodGet {
		actor, ok := h.actor(w, r, "repository.request.read", reqID)
		if !ok {
			return
		}
		req, found := h.service.GetRequest(reqID)
		if !found {
			http.Error(w, `{"error":"Không tìm thấy yêu cầu"}`, http.StatusNotFound)
			return
		}
		if actor.Role != "ADMIN" && (req.Submitter != actor.ID || (actor.WorkspaceID != "" && req.TargetWorkspace != actor.WorkspaceID)) {
			http.Error(w, `{"error":"Không tìm thấy yêu cầu"}`, http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(req)
		return
	}

	http.Error(w, `{"error":"Method not allowed"}`, http.StatusMethodNotAllowed)
}

func (h *HTTPHandler) handleDeliver(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, `{"error":"Method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
	var payload DeliverPayload
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		http.Error(w, `{"error":"Dữ liệu phân phối không hợp lệ"}`, http.StatusBadRequest)
		return
	}
	actor, ok := h.actor(w, r, "repository.artifact.deliver", payload.RequestID)
	if !ok {
		return
	}
	if !requireRepositoryAdmin(w, actor) {
		return
	}

	// The public endpoint does not pass client-controlled filesystem paths to the
	// service. Delivery remains unavailable until a trusted workspace registry can
	// resolve the request's workspace to a canonical destination.
	if err := h.service.Deliver(payload.RequestID, ""); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		_ = json.NewEncoder(w).Encode(map[string]any{"success": false, "status": "UNAVAILABLE", "error": err.Error()})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"success":    true,
		"request_id": payload.RequestID,
		"dest_dir":   payload.TargetDirectory,
		"message":    "Phân phối mã nguồn đã xác minh thành công vào workspace",
	})
}

func (h *HTTPHandler) handleAudit(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSONError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	actor, ok := h.actor(w, r, "repository.audit.read", "")
	if !ok {
		return
	}
	if !requireRepositoryAdmin(w, actor) {
		return
	}
	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		if parsed, err := strconv.Atoi(l); err == nil && parsed > 0 {
			limit = parsed
		}
	}

	events := h.service.GetAuditEvents(limit)
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(events)
}
