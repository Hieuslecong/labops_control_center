package gateway

import (
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	DelegationAudience     = "labops-node-execution"
	MaxDelegationTTLSec    = int64(300)
	MaxDelegationClockSkew = int64(30)
	MaxReplayGuardEntries  = 4096
)

// DelegationClaims ràng buộc quyền thực thi ngắn hạn theo từng hành động/node/workspace cụ thể.
type DelegationClaims struct {
	ActorID      string `json:"actor_id"`
	ActorRole    string `json:"actor_role"` // ADMIN | RESEARCHER | SYSTEM
	ServiceID    string `json:"service_id"`
	Action       string `json:"action"`
	NodeID       string `json:"node_id"`
	WorkspaceID  string `json:"workspace_id,omitempty"`
	ResourceRef  string `json:"resource_ref,omitempty"`
	Audience     string `json:"aud"`
	RequestID    string `json:"request_id"`
	Nonce        string `json:"nonce"`
	ParamsSHA256 string `json:"params_sha256"`
	IssuedAt     int64  `json:"iat"`
	ExpiresAt    int64  `json:"exp"`
}

// ComputeParamsHash tạo SHA-256 chuẩn hóa theo thứ tự key của parameters map.
func ComputeParamsHash(params map[string]string) string {
	keys := make([]string, 0, len(params))
	for k := range params {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	h := sha256.New()
	for _, k := range keys {
		h.Write([]byte(k))
		h.Write([]byte{0})
		h.Write([]byte(params[k]))
		h.Write([]byte{0})
	}
	return hex.EncodeToString(h.Sum(nil))
}

// SignDelegationToken ký HMAC-SHA256 cho DelegationClaims.
func SignDelegationToken(secret string, claims DelegationClaims) (string, error) {
	if strings.TrimSpace(secret) == "" {
		return "", errors.New("delegation secret is not configured")
	}
	if strings.TrimSpace(claims.Audience) == "" {
		claims.Audience = DelegationAudience
	}
	if claims.Audience != DelegationAudience {
		return "", errors.New("invalid delegation audience")
	}
	if claims.IssuedAt <= 0 {
		claims.IssuedAt = time.Now().Unix()
	}
	if claims.ExpiresAt <= claims.IssuedAt || (claims.ExpiresAt-claims.IssuedAt) > MaxDelegationTTLSec {
		return "", fmt.Errorf("delegation TTL must be between 1s and %ds", MaxDelegationTTLSec)
	}
	if strings.TrimSpace(claims.ActorID) == "" || strings.TrimSpace(claims.ActorRole) == "" ||
		strings.TrimSpace(claims.ServiceID) == "" || strings.TrimSpace(claims.Action) == "" ||
		strings.TrimSpace(claims.NodeID) == "" || strings.TrimSpace(claims.RequestID) == "" ||
		strings.TrimSpace(claims.Nonce) == "" {
		return "", errors.New("missing required delegation claims")
	}

	payload, err := json.Marshal(claims)
	if err != nil {
		return "", err
	}
	encodedPayload := base64.RawURLEncoding.EncodeToString(payload)
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(encodedPayload))
	encodedSig := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	return encodedPayload + "." + encodedSig, nil
}

// VerifyDelegationToken xác minh chữ ký HMAC-SHA256, hạn dùng, audience và toàn bộ claim bắt buộc.
func VerifyDelegationToken(secret, token string, now time.Time) (*DelegationClaims, error) {
	if strings.TrimSpace(secret) == "" {
		return nil, errors.New("delegation secret is not configured (fail-closed)")
	}
	parts := strings.Split(strings.TrimSpace(token), ".")
	if len(parts) != 2 || parts[0] == "" || parts[1] == "" {
		return nil, errors.New("malformed delegation token")
	}

	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(parts[0]))
	expectedSig := mac.Sum(nil)

	providedSig, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil || len(providedSig) != len(expectedSig) || subtle.ConstantTimeCompare(providedSig, expectedSig) != 1 {
		return nil, errors.New("invalid delegation token signature")
	}

	payloadBytes, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return nil, errors.New("invalid delegation token payload encoding")
	}

	var claims DelegationClaims
	if err := json.Unmarshal(payloadBytes, &claims); err != nil {
		return nil, errors.New("invalid delegation token JSON")
	}

	if claims.Audience != DelegationAudience {
		return nil, fmt.Errorf("invalid delegation audience '%s'", claims.Audience)
	}
	if strings.TrimSpace(claims.ActorID) == "" || strings.TrimSpace(claims.ServiceID) == "" ||
		strings.TrimSpace(claims.Action) == "" || strings.TrimSpace(claims.NodeID) == "" ||
		strings.TrimSpace(claims.RequestID) == "" || strings.TrimSpace(claims.Nonce) == "" {
		return nil, errors.New("incomplete delegation claims")
	}
	role := strings.ToUpper(strings.TrimSpace(claims.ActorRole))
	if role != "ADMIN" && role != "RESEARCHER" && role != "SYSTEM" {
		return nil, fmt.Errorf("unsupported delegation actor_role '%s'", claims.ActorRole)
	}
	claims.ActorRole = role

	nowUnix := now.Unix()
	if claims.IssuedAt <= 0 || claims.ExpiresAt <= claims.IssuedAt {
		return nil, errors.New("invalid delegation timestamps")
	}
	if (claims.ExpiresAt - claims.IssuedAt) > MaxDelegationTTLSec {
		return nil, errors.New("delegation lifetime exceeds maximum allowed TTL")
	}
	if claims.IssuedAt > nowUnix+MaxDelegationClockSkew {
		return nil, errors.New("delegation token issued in the future")
	}
	if nowUnix > claims.ExpiresAt {
		return nil, errors.New("delegation token expired")
	}

	return &claims, nil
}

// ActionAuthorizationCheck kiểm tra action-scoped authorization ở cả Gateway và Node Agent.
func ActionAuthorizationCheck(claims *DelegationClaims, reqNodeID, reqAction string, params map[string]string) error {
	if claims == nil {
		return errors.New("missing verified delegation claims")
	}
	if !AllowedActions[reqAction] {
		return fmt.Errorf("action '%s' is not in gateway allowlist", reqAction)
	}
	if !strings.EqualFold(claims.NodeID, reqNodeID) {
		return fmt.Errorf("delegation node mismatch: token='%s' request='%s'", claims.NodeID, reqNodeID)
	}
	if claims.Action != reqAction {
		return fmt.Errorf("delegation action mismatch: token='%s' request='%s'", claims.Action, reqAction)
	}
	if claims.ParamsSHA256 != "" {
		actualHash := ComputeParamsHash(params)
		if subtle.ConstantTimeCompare([]byte(claims.ParamsSHA256), []byte(actualHash)) != 1 {
			return errors.New("delegation parameters digest mismatch")
		}
	}

	if claims.ActorRole == "ADMIN" {
		return nil
	}

	if claims.ActorRole == "SYSTEM" {
		switch reqAction {
		case "INSPECT_HOST", "INSPECT_GPUS", "COLLECT_TELEMETRY", "LIST_STORAGE_POOLS", "INSPECT_STORAGE_POOL", "GET_RESOURCE_PROFILE":
			return nil
		default:
			return fmt.Errorf("SYSTEM service role is not authorized for privileged action '%s'", reqAction)
		}
	}

	// RESEARCHER role is strictly denied all Admin infrastructure/privileged actions
	switch reqAction {
	case "PROVISION_DOCKER_WORKSPACE", "ARCHIVE_REMOVE_DOCKER_WORKSPACE", "KILL_PROCESS", "REPO_DECISION", "SYNC_SSH_AUTHORIZED_KEYS", "VERIFY_DOCKER_WORKSPACE", "INSPECT_WIREGUARD_PEERS", "INSPECT_HOST", "INSPECT_GPUS", "COLLECT_TELEMETRY", "PROVISION_MEMBER_STORAGE", "VALIDATE_STORAGE_ALLOCATION":
		return fmt.Errorf("RESEARCHER '%s' is forbidden from privileged action '%s'", claims.ActorID, reqAction)
	case "STOP_DOCKER_WORKSPACE", "TERMINATE_PROCESS", "VERIFY_PROCESS_PID", "REPO_DELIVER":
		if strings.TrimSpace(claims.WorkspaceID) == "" {
			return fmt.Errorf("RESEARCHER '%s' action '%s' requires explicit workspace_id scope", claims.ActorID, reqAction)
		}
		if wsParam := strings.TrimSpace(params["workspace_id"]); wsParam != "" && wsParam != claims.WorkspaceID {
			return fmt.Errorf("cross-workspace access denied: token='%s' param='%s'", claims.WorkspaceID, wsParam)
		}
		if ownerParam := strings.TrimSpace(params["owner"]); ownerParam != "" && ownerParam != claims.ActorID {
			return fmt.Errorf("cross-user owner access denied: actor='%s' param='%s'", claims.ActorID, ownerParam)
		}
		if claims.ResourceRef != "" {
			if ref := strings.TrimSpace(params["ref"]); ref != "" && ref != claims.ResourceRef {
				return fmt.Errorf("resource_ref mismatch for scoped researcher action")
			}
			if cid := strings.TrimSpace(params["container_id"]); cid != "" && cid != claims.ResourceRef {
				return fmt.Errorf("container_id mismatch for scoped researcher action")
			}
		}
		return nil
	default:
		return fmt.Errorf("RESEARCHER is not permitted to invoke '%s'", reqAction)
	}
}

type idempotencyEntry struct {
	ParamsHash string
	Response   *DispatchResponse
	ExpiresAt  time.Time
}

// ReplayGuard chống dùng lại nonce và ràng buộc Idempotency-Key với actor+action+params.
type ReplayGuard struct {
	mu          sync.Mutex
	nonces      map[string]time.Time
	idempotency map[string]idempotencyEntry
	maxEntries  int
}

func NewReplayGuard() *ReplayGuard {
	return &ReplayGuard{
		nonces:      make(map[string]time.Time),
		idempotency: make(map[string]idempotencyEntry),
		maxEntries:  MaxReplayGuardEntries,
	}
}

func (rg *ReplayGuard) purgeExpiredLocked(now time.Time) {
	for k, exp := range rg.nonces {
		if now.After(exp) {
			delete(rg.nonces, k)
		}
	}
	for k, ent := range rg.idempotency {
		if now.After(ent.ExpiresAt) {
			delete(rg.idempotency, k)
		}
	}
	for len(rg.nonces) > rg.maxEntries {
		for k := range rg.nonces {
			delete(rg.nonces, k)
			break
		}
	}
	for len(rg.idempotency) > rg.maxEntries {
		for k := range rg.idempotency {
			delete(rg.idempotency, k)
			break
		}
	}
}

// ReserveNonce từ chối ngay nếu nonce đã từng được thấy trước khi hết hạn.
func (rg *ReplayGuard) ReserveNonce(nonce string, expiresAt time.Time) error {
	if strings.TrimSpace(nonce) == "" {
		return errors.New("nonce không được rỗng")
	}
	now := time.Now()
	rg.mu.Lock()
	defer rg.mu.Unlock()
	rg.purgeExpiredLocked(now)
	if exp, exists := rg.nonces[nonce]; exists && now.Before(exp) {
		return fmt.Errorf("REPLAY_DETECTED: delegation nonce '%s' đã được sử dụng", nonce)
	}
	if expiresAt.IsZero() {
		expiresAt = now.Add(time.Duration(MaxDelegationTTLSec) * time.Second)
	}
	rg.nonces[nonce] = expiresAt.Add(time.Duration(MaxDelegationClockSkew) * time.Second)
	return nil
}

// CheckIdempotency kiểm tra nếu key đã tồn tại:
// - Khác params -> trả lỗi IDEMPOTENCY_CONFLICT.
// - Trùng params -> trả cached DispatchResponse (copy).
func (rg *ReplayGuard) CheckIdempotency(actor, action, key, paramsHash string) (*DispatchResponse, bool, error) {
	if strings.TrimSpace(key) == "" {
		return nil, false, nil
	}
	scopeKey := actor + "|" + action + "|" + key
	now := time.Now()
	rg.mu.Lock()
	defer rg.mu.Unlock()
	rg.purgeExpiredLocked(now)
	ent, ok := rg.idempotency[scopeKey]
	if !ok {
		return nil, false, nil
	}
	if ent.ParamsHash != paramsHash {
		return nil, false, errors.New("IDEMPOTENCY_CONFLICT: idempotency_key đã được dùng cho payload khác")
	}
	if ent.Response == nil {
		return nil, false, nil
	}
	cp := *ent.Response
	return &cp, true, nil
}

// RecordIdempotency lưu kết quả theo (actor, action, key, paramsHash).
func (rg *ReplayGuard) RecordIdempotency(actor, action, key, paramsHash string, resp *DispatchResponse, ttl time.Duration) {
	if strings.TrimSpace(key) == "" || resp == nil {
		return
	}
	if ttl <= 0 {
		ttl = 10 * time.Minute
	}
	scopeKey := actor + "|" + action + "|" + key
	cp := *resp
	rg.mu.Lock()
	defer rg.mu.Unlock()
	rg.purgeExpiredLocked(time.Now())
	rg.idempotency[scopeKey] = idempotencyEntry{
		ParamsHash: paramsHash,
		Response:   &cp,
		ExpiresAt:  time.Now().Add(ttl),
	}
}
