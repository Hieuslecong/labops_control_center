package repositoryguard

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
)

type HTTPHandler struct {
	service *GuardService
}

func NewHTTPHandler(service *GuardService) *HTTPHandler {
	return &HTTPHandler{service: service}
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
		submitter := r.URL.Query().Get("submitter")
		isAdmin := r.URL.Query().Get("is_admin") == "true" || submitter == ""
		list := h.service.ListRequests(submitter, isAdmin)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(list)

	case http.MethodPost:
		var payload struct {
			RepoURL         string `json:"repo_url"`
			Ref             string `json:"ref"`
			Submitter       string `json:"submitter"`
			TargetWorkspace string `json:"target_workspace"`
		}
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			http.Error(w, `{"error":"Dữ liệu JSON không hợp lệ"}`, http.StatusBadRequest)
			return
		}

		if payload.Submitter == "" {
			payload.Submitter = "researcher"
		}

		req, err := h.service.SubmitRequest(r.Context(), payload.RepoURL, payload.Ref, payload.Submitter, payload.TargetWorkspace)
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

	if len(parts) > 1 && parts[1] == "decision" && r.Method == http.MethodPost {
		var payload struct {
			Decision string `json:"decision"`
			Reviewer string `json:"reviewer"`
			Reason   string `json:"reason"`
		}
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			http.Error(w, `{"error":"Dữ liệu quyết định không hợp lệ"}`, http.StatusBadRequest)
			return
		}

		updated, err := h.service.AdminDecision(reqID, payload.Decision, payload.Reviewer, payload.Reason)
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
		req, found := h.service.GetRequest(reqID)
		if !found {
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

	var payload DeliverPayload
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		http.Error(w, `{"error":"Dữ liệu phân phối không hợp lệ"}`, http.StatusBadRequest)
		return
	}

	if err := h.service.Deliver(payload.RequestID, payload.TargetDirectory); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
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
