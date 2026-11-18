# 07 — XÁC MINH GATEWAY ZERO-TRUST & ỦY QUYỀN mTLS

- **Branch**: `hardening/labops-v2`
- **Thành phần liên quan**:
  - Go PKI: `go/internal/pki/pki.go`, `go/internal/pki/pki_test.go`
  - Go Gateway: `go/internal/gateway/{server,session,http_api,delegation,types}.go`, `go/internal/gateway/gateway_test.go`
  - Go Node Agent Tunnel & Transport: `go/internal/agent/tunnel_client.go`, `go/internal/transport/server.go`
  - Python Delegation: `backend/app/core/delegation.py`

---

## 1. Kiến trúc Zero-Trust Gateway ↔ Node Agent

1. **Hạ tầng khóa công khai PKI (`go/internal/pki/pki.go`)**:
   - `GenerateOrLoadCA`, `IssueGatewayCert`, `IssueNodeCert` phát hành chứng chỉ X.509 kèm định danh SPIFFE SAN `spiffe://labops/node/<node_id>` và xác thực qua `IsValidNodeID`.
   - `BuildServerTLSConfig` & `BuildClientTLSConfig` bắt buộc **TLS 1.3 mTLS** hai chiều và đối chiếu danh sách thu hồi chứng chỉ bền vững trên đĩa (`NewRevocationManagerWithPersistence`).
   - `EnrollmentTokenManager` ký enrollment token ngắn hạn gắn chặt với node đăng ký; sau khi thu hồi (`RevokeRequest`), HTTP Control Plane (`http_api.go`) không tự động hủy thu hồi trái phép (`TestHTTPControlPlane_NoAutoUnrevoke`).
2. **Định danh & Chống Mạo danh Chéo Node**:
   - `ExtractNodeIDFromCert` gắn phiên làm việc trong `SessionManager` với node ID thật trên chứng chỉ mTLS; phản hồi từ agent khác giả danh node sẽ bị `RouteCommandResponse` từ chối (`TestRouteCommandResponse_RejectsCrossNodeSpoof`).
   - Fix race condition khi agent kết nối lại (`TestSessionReconnectRaceDoesNotMarkNewSessionOffline`).
3. **Ủy quyền Thao tác & Chống Replay**:
   - Python (`build_delegation_context`, `compute_params_hash`) và Go (`SignDelegationToken`, `VerifyDelegationToken`, `ActionAuthorizationCheck`) băm chặt tham số hành động (`ParamsHash`) kèm `DELEGATION_AUDIENCE`.
   - `ReplayGuard` chặn dùng lại token ủy quyền đã tiêu thụ (`TestGatewayDelegationAndReplayProtection`).

---

## 2. Kết quả Kiểm thử

| Lệnh & Gói kiểm thử | Kết quả | Chi tiết |
|---|---|---|
| `go test -race ./internal/pki` | **4/4 PASS (1.100s)** | `TestPKIWorkflowAndTLSHandshake`, `TestEnrollmentTokenManager`, `TestRevocationPersistenceAcrossRestart`, `TestPKISecurityFailClosedAndRevocationInvariants` |
| `go test -race ./internal/gateway` | **7/7 PASS (1.109s)** | `TestSessionManagerLifecycleAndDispatch`, `TestHTTPControlPlaneAPI`, `TestGRPCGatewayMTLSTunnel`, `TestSessionReconnectRaceDoesNotMarkNewSessionOffline`, `TestGatewayDelegationAndReplayProtection`, `TestRouteCommandResponse_RejectsCrossNodeSpoof`, `TestHTTPControlPlane_NoAutoUnrevoke` |
| `go test -race ./internal/agent ./internal/transport` | **2/2 PASS** | Tunnel client & gRPC/HTTP transport tests |
| `pytest backend/tests/test_gateway_integration.py` | **PASS** | Xác minh parser telemetry gateway và allowlist điều phối |

---

## 3. Hạn chế Còn lại
- Luồng mTLS hai chiều giữa Gateway (`aiot-ws1`) và Agent trên `aiotlab-rack` (`192.168.167.250`) mới được xác minh bằng loopback mTLS gRPC test trong Go (`TestGRPCGatewayMTLSTunnel`) do máy chủ Rack đang ngoại tuyến.
