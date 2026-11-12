# LABOPS INTERNAL PKI & MTLS CERTIFICATE MANAGEMENT

## 1. MÔ HÌNH HẠ TẦNG KHÓA CÔNG KHAI (INTERNAL PKI)

LabOps Control Center tích hợp bộ máy quản lý chứng chỉ số nội bộ độc lập (Internal PKI Engine) được lập trình bằng Go (`go/internal/pki/pki.go`), sử dụng mật mã đường cong elliptic hiện đại **ECDSA P-256** với thuật toán băm **SHA-256**.

### Phân cấp chứng chỉ
1. **LabOps Root CA**:
   - Tên định danh (CN): `LabOps Control Center Root CA (V2)`
   - Tổ chức (O): `LabOps AI Infrastructure`
   - Đơn vị (OU): `Security Root CA`
   - Thời hạn hiệu lực: 10 năm.
   - Vị trí lưu trữ: `~/.labops/pki/labops-ca.crt` (public) và `~/.labops/pki/labops-ca.key` (private, quyền `0600`).
2. **Gateway Server Certificate**:
   - CN: `gateway.control-plane.labops.local`
   - SANs: `127.0.0.1`, `localhost`, `aiotlab-rack`, `192.168.167.250`, `gateway.control-plane.labops.local`.
   - Mục đích: `x509.ExtKeyUsageServerAuth`.
3. **Node Client Certificate**:
   - CN: `<node_id>` (ví dụ: `workstation`, `track`).
   - SANs: `<node_id>.node.labops.local`, `<node_id>`.
   - Mục đích: `x509.ExtKeyUsageClientAuth`.

---

## 2. QUY TRÌNH GHI DANH NÚT TỰ ĐỘNG (ZERO-TOUCH ENROLLMENT)

Để đảm bảo việc cấp chứng chỉ cho máy chủ mới không yêu cầu sao chép thủ công khóa riêng tư qua các kênh kém bảo mật, LabOps áp dụng giao thức **One-Time Enrollment Token**:

```
[ Workstation / Node ]                                          [ Control Plane Gateway ]
       |                                                                   |
       | ----- (1) POST /api/v1/gateway/enroll {node_id} ------------->   |
       |                                                                   | (Tạo One-time Token có TTL 15 phút)
       | <---- (2) Phản hồi JSON {token, expires_in} --------------------- |
       |                                                                   |
       | ----- (3) POST /api/v1/gateway/issue-cert {node_id, token} -----> |
       |                                                                   | (Xác thực & Tiêu thụ Token 1 lần)
       |                                                                   | (Tạo cặp khóa & ký cert với SAN node_id)
       | <---- (4) Phản hồi JSON {ca_crt, client_crt, client_key} -------- |
       |                                                                   |
  (Lưu cert vào /etc/labops/certs/ với quyền 0600)                         |
       |                                                                   |
       | ===== (5) Kết nối gRPC mTLS TLS 1.3 (TCP 9443) ================> |
       |                                                                   | (Verify SAN <node_id>.node.labops.local)
```

### Chống Replay Attack
- Token ghi danh chỉ được sử dụng đúng **một lần duy nhất**.
- Ngay sau khi tiêu thụ thành công, token bị xóa khỏi bộ nhớ của Gateway.
- Token tự động vô hiệu hóa nếu vượt quá thời gian sống (TTL mặc định: 15 phút).

---

## 3. THU HỒI CHỨNG CHỈ (REVOCATION MANAGEMENT)

Khi một compute node bị nghi ngờ xâm nhập hoặc loại biên:
1. Quản trị viên gọi API thu hồi:
   ```bash
   curl -X POST http://127.0.0.1:9444/api/v1/gateway/revoke \
     -H "Content-Type: application/json" \
     -d '{"node_id": "workstation", "reason": "Bảo trì định kỳ hoặc nghi ngờ xâm nhập"}'
   ```
2. `RevocationManager` ghi nhận node vào danh sách đen và lập tức ngắt phiên stream gRPC hiện tại.
3. Callback xác minh TLS `VerifyPeerCertificate` tại Gateway sẽ từ chối mọi nỗ lực bắt tay mTLS tiếp theo từ node này, trả về mã lỗi:
   `mTLS: Node ID 'workstation' đã bị từ chối hoặc thu hồi quyền truy cập`.
