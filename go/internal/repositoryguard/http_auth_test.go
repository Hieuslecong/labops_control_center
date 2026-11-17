package repositoryguard

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestRepositoryGuardDoesNotTrustClientClaimedAdminForListing(t *testing.T) {
	svc, err := NewGuardService(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	handler := NewHTTPHandler(svc)
	req := httptest.NewRequest("GET", "/api/v1/agent/repositoryguard/requests?submitter=alice&is_admin=true", nil)
	response := httptest.NewRecorder()
	handler.handleRequests(response, req)
	if response.Code != 503 {
		t.Fatalf("untrusted client admin claim returned HTTP %d, want fail-closed 503", response.Code)
	}
}

func TestRepositoryGuardListingScopesToAuthenticatedActor(t *testing.T) {
	svc, err := NewGuardService(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	svc.requests["req-aaaaaaaaaaaa"] = &RepositoryRequest{ID: "req-aaaaaaaaaaaa", Submitter: "alice"}
	svc.requests["req-bbbbbbbbbbbb"] = &RepositoryRequest{ID: "req-bbbbbbbbbbbb", Submitter: "bob"}
	resolver := RepositoryActorResolver(func(_ *http.Request, _, _ string) (RepositoryActor, error) {
		return RepositoryActor{ID: "alice", Role: "RESEARCHER"}, nil
	})
	handler := NewHTTPHandler(svc, resolver)
	req := httptest.NewRequest("GET", "/api/v1/agent/repositoryguard/requests?submitter=bob&is_admin=true", nil)
	response := httptest.NewRecorder()
	handler.handleRequests(response, req)
	if response.Code != 200 {
		t.Fatalf("authenticated researcher listing returned HTTP %d", response.Code)
	}
	var listed []RepositoryRequest
	if err := json.Unmarshal(response.Body.Bytes(), &listed); err != nil {
		t.Fatal(err)
	}
	if len(listed) != 1 || listed[0].Submitter != "alice" {
		t.Fatalf("listing followed client identity claims: %#v", listed)
	}
}
