package repositoryguard

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"net/http/httptest"
	"testing"
	"time"
)

func signedTestScopedToken(t *testing.T, secret string, claims map[string]any) string {
	t.Helper()
	payload, err := json.Marshal(claims)
	if err != nil {
		t.Fatal(err)
	}
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write(payload)
	encode := base64.RawURLEncoding.EncodeToString
	return encode(payload) + "." + encode(mac.Sum(nil))
}

func validTestClaims(now time.Time) map[string]any {
	return map[string]any{
		"iss":          "labops-aiotgit",
		"aud":          "labops-repositoryguard",
		"sub":          "alice",
		"role":         "RESEARCHER",
		"member_id":    "member-alice",
		"workspace_id": "ws-0123456789",
		"node_id":      "track",
		"scopes":       []string{"repo:request:submit", "repo:request:list", "repo:request:read", "repo:bundle:read"},
		"resource_id":  "",
		"jti":          "0123456789abcdef0123456789abcdef",
		"iat":          now.Unix(),
		"exp":          now.Add(5 * time.Minute).Unix(),
	}
}

func TestVerifyScopedTokenAcceptsSignedWorkspaceScopedClaims(t *testing.T) {
	now := time.Unix(1_800_000_000, 0)
	secret := "0123456789abcdef0123456789abcdef"
	token := signedTestScopedToken(t, secret, validTestClaims(now))

	claims, err := VerifyScopedToken(token, secret, now)
	if err != nil {
		t.Fatalf("VerifyScopedToken() error = %v", err)
	}
	if claims.Subject != "alice" || claims.WorkspaceID != "ws-0123456789" || claims.NodeID != "track" {
		t.Fatalf("unexpected claims: %+v", claims)
	}
}

func TestVerifyScopedTokenRejectsInvalidOrExpiredCapabilities(t *testing.T) {
	now := time.Unix(1_800_000_000, 0)
	secret := "0123456789abcdef0123456789abcdef"
	tests := []struct {
		name   string
		mutate func(map[string]any)
		key    string
	}{
		{name: "wrong audience", mutate: func(c map[string]any) { c["aud"] = "other" }, key: secret},
		{name: "expired", mutate: func(c map[string]any) { c["exp"] = now.Unix() }, key: secret},
		{name: "too long", mutate: func(c map[string]any) { c["exp"] = now.Add(16 * time.Minute).Unix() }, key: secret},
		{name: "invalid workspace", mutate: func(c map[string]any) { c["workspace_id"] = "../../other" }, key: secret},
		{name: "wrong signing key", mutate: func(c map[string]any) {}, key: "abcdef0123456789abcdef0123456789"},
		{name: "short key", mutate: func(c map[string]any) {}, key: "too-short"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			claims := validTestClaims(now)
			tt.mutate(claims)
			token := signedTestScopedToken(t, secret, claims)
			if _, err := VerifyScopedToken(token, tt.key, now); err == nil {
				t.Fatal("invalid scoped token was accepted")
			}
		})
	}
}

func TestScopedTokenResolverBindsActionNodeAndWorkspace(t *testing.T) {
	now := time.Now()
	secret := "0123456789abcdef0123456789abcdef"
	token := signedTestScopedToken(t, secret, validTestClaims(now))
	req := httptest.NewRequest("POST", "/api/v1/agent/repositoryguard/requests", nil)
	req.Header.Set("Authorization", "Bearer "+token)

	resolver := NewScopedTokenResolver(secret, "track")
	actor, err := resolver(req, "repository.request.submit", "ws-0123456789")
	if err != nil {
		t.Fatalf("valid workspace request rejected: %v", err)
	}
	if actor.ID != "alice" || actor.Role != "RESEARCHER" || actor.WorkspaceID != "ws-0123456789" {
		t.Fatalf("unexpected authenticated actor: %+v", actor)
	}
	if _, err := resolver(req, "repository.request.decide", "req-aaaaaaaaaaaa"); err == nil {
		t.Fatal("researcher token was allowed to decide an admin request")
	}
	if _, err := NewScopedTokenResolver(secret, "workstation")(req, "repository.request.submit", "ws-0123456789"); err == nil {
		t.Fatal("token for a different node was accepted")
	}
	if _, err := resolver(req, "repository.request.submit", "ws-aaaaaaaaaa"); err == nil {
		t.Fatal("token was allowed to submit to another workspace")
	}
}
