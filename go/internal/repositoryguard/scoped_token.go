package repositoryguard

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
	"strings"
	"time"
)

const (
	scopedTokenIssuer   = "labops-aiotgit"
	scopedTokenAudience = "labops-repositoryguard"
	maxScopedTokenTTL   = 15 * time.Minute
)

var (
	workspaceTokenIDRE = regexp.MustCompile(`^ws-[a-f0-9]{10}$`)
	subjectTokenIDRE   = regexp.MustCompile(`^[A-Za-z0-9_.@-]{1,128}$`)
	nodeTokenIDRE      = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
	allowedRepoScopes  = map[string]struct{}{
		"repo:request:submit": {},
		"repo:request:list":   {},
		"repo:request:read":   {},
		"repo:bundle:read":    {},
		"repo:request:decide": {},
		"repo:audit:read":     {},
	}
)

// ScopedTokenClaims is a short-lived capability issued by the authenticated
// Backend. It carries the human identity and the workspace/node scope; callers
// must not replace these values with request fields or headers.
type ScopedTokenClaims struct {
	Issuer      string   `json:"iss"`
	Audience    string   `json:"aud"`
	Subject     string   `json:"sub"`
	Role        string   `json:"role"`
	MemberID    string   `json:"member_id"`
	WorkspaceID string   `json:"workspace_id"`
	NodeID      string   `json:"node_id"`
	Scopes      []string `json:"scopes"`
	ResourceID  string   `json:"resource_id"`
	TokenID     string   `json:"jti"`
	IssuedAt    int64    `json:"iat"`
	ExpiresAt   int64    `json:"exp"`
}

// VerifyScopedToken validates the Python-compatible HMAC token format used by
// the Backend. The HMAC covers the exact encoded JSON bytes, so field ordering
// is irrelevant to verification while duplicate and unknown fields are denied.
func VerifyScopedToken(token, secret string, now time.Time) (ScopedTokenClaims, error) {
	var claims ScopedTokenClaims
	if len(secret) < 32 {
		return claims, errors.New("repository token signing key must contain at least 32 characters")
	}
	if len(token) > 16<<10 || strings.Count(token, ".") != 1 {
		return claims, errors.New("invalid repository capability token")
	}
	parts := strings.SplitN(token, ".", 2)
	payload, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil || len(payload) == 0 || len(payload) > 8<<10 {
		return claims, errors.New("invalid repository capability payload")
	}
	signature, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil || len(signature) != sha256.Size {
		return claims, errors.New("invalid repository capability signature")
	}
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write(payload)
	if !hmac.Equal(signature, mac.Sum(nil)) {
		return claims, errors.New("repository capability signature mismatch")
	}
	if err := rejectDuplicateJSONKeys(payload); err != nil {
		return claims, fmt.Errorf("invalid repository capability payload: %w", err)
	}
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&claims); err != nil {
		return claims, fmt.Errorf("decode repository capability: %w", err)
	}
	if err := decoder.Decode(new(any)); !errors.Is(err, io.EOF) {
		return claims, errors.New("repository capability contains trailing JSON")
	}
	if claims.Issuer != scopedTokenIssuer || claims.Audience != scopedTokenAudience ||
		!subjectTokenIDRE.MatchString(claims.Subject) ||
		(claims.Role != "RESEARCHER" && claims.Role != "ADMIN") ||
		!nodeTokenIDRE.MatchString(claims.NodeID) || len(claims.TokenID) < 16 || len(claims.TokenID) > 128 {
		return claims, errors.New("repository capability identity is invalid")
	}
	if claims.Role == "RESEARCHER" && claims.MemberID == "" {
		return claims, errors.New("researcher capability must be bound to a member")
	}
	if claims.Role == "RESEARCHER" && claims.WorkspaceID != "" && !workspaceTokenIDRE.MatchString(claims.WorkspaceID) {
		return claims, errors.New("researcher capability workspace is invalid")
	}
	if claims.Role == "ADMIN" && claims.WorkspaceID != "" && !workspaceTokenIDRE.MatchString(claims.WorkspaceID) {
		return claims, errors.New("admin capability workspace is invalid")
	}
	if claims.MemberID != "" && !subjectTokenIDRE.MatchString(claims.MemberID) {
		return claims, errors.New("repository capability member identity is invalid")
	}
	if len(claims.Scopes) == 0 || len(claims.Scopes) > len(allowedRepoScopes) {
		return claims, errors.New("repository capability scopes are invalid")
	}
	seenScopes := make(map[string]struct{}, len(claims.Scopes))
	for _, scope := range claims.Scopes {
		if _, ok := allowedRepoScopes[scope]; !ok {
			return claims, errors.New("repository capability contains an unsupported scope")
		}
		if _, duplicate := seenScopes[scope]; duplicate {
			return claims, errors.New("repository capability contains duplicate scopes")
		}
		seenScopes[scope] = struct{}{}
	}
	if claims.Role == "RESEARCHER" && claims.WorkspaceID == "" {
		for scope := range seenScopes {
			if scope != "repo:request:list" && scope != "repo:request:read" {
				return claims, errors.New("unbound researcher capability may only read own request metadata")
			}
		}
	}
	if len(claims.ResourceID) > 128 || strings.ContainsAny(claims.ResourceID, "\r\n\x00") {
		return claims, errors.New("repository capability resource is invalid")
	}
	current := now.Unix()
	if claims.IssuedAt > current+30 || claims.ExpiresAt <= current || claims.ExpiresAt <= claims.IssuedAt ||
		claims.ExpiresAt-claims.IssuedAt > int64(maxScopedTokenTTL/time.Second) {
		return claims, errors.New("repository capability has expired or invalid lifetime")
	}
	return claims, nil
}

func rejectDuplicateJSONKeys(data []byte) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err := consumeJSONValue(decoder); err != nil {
		return err
	}
	if _, err := decoder.Token(); !errors.Is(err, io.EOF) {
		if err == nil {
			return errors.New("trailing JSON value")
		}
		return err
	}
	return nil
}

func consumeJSONValue(decoder *json.Decoder) error {
	token, err := decoder.Token()
	if err != nil {
		return err
	}
	delim, isDelim := token.(json.Delim)
	if !isDelim {
		return nil
	}
	switch delim {
	case '{':
		seen := map[string]struct{}{}
		for decoder.More() {
			keyToken, err := decoder.Token()
			if err != nil {
				return err
			}
			key, ok := keyToken.(string)
			if !ok {
				return errors.New("object key is not a string")
			}
			if _, exists := seen[key]; exists {
				return errors.New("duplicate object key")
			}
			seen[key] = struct{}{}
			if err := consumeJSONValue(decoder); err != nil {
				return err
			}
		}
		end, err := decoder.Token()
		if err != nil || end != json.Delim('}') {
			return errors.New("malformed object")
		}
	case '[':
		for decoder.More() {
			if err := consumeJSONValue(decoder); err != nil {
				return err
			}
		}
		end, err := decoder.Token()
		if err != nil || end != json.Delim(']') {
			return errors.New("malformed array")
		}
	default:
		return errors.New("unexpected JSON delimiter")
	}
	return nil
}
