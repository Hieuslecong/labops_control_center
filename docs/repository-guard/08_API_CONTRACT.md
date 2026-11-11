# LABOPS REPOSITORY GUARD — REST API CONTRACT (PHASE 11 & 19)
**Base Path:** `/api/v1/repositories` (qua Next.js BFF: `/api/labops/repositories`)  
**Authentication:** Bearer Token / Clerk Session + HMAC Machine Token  

---

## 1. ENDPOINTS REFERENCE

### 1.1. Submit Repository Request
- **Endpoint**: `POST /api/v1/repositories/requests`
- **Quyền**: `require_user`
- **Request Body**:
  ```json
  {
    "repo_url": "https://github.com/gin-gonic/gin",
    "ref": "v1.9.1",
    "target_workspace": "ws-researcher"
  }
  ```
- **Response**: `202 Accepted`
  ```json
  {
    "id": "req-9a8b7c6d5e4f",
    "repo_url": "https://github.com/gin-gonic/gin",
    "canonical_repo": "https://github.com/gin-gonic/gin",
    "commit_sha": "a1b2c3d4e5f6789012345678901234567890abcd",
    "state": "SUBMITTED",
    "submitted_by": "researcher",
    "policy_version": "labops-repo-policy-2026.11-v2"
  }
  ```

---

### 1.2. List Repository Requests
- **Endpoint**: `GET /api/v1/repositories/requests`
- **Quyền**: `require_user` (Admin xem tất cả; Researcher xem yêu cầu của mình)
- **Response**: `200 OK` (Mảng các `RepositoryRequest`).

---

### 1.3. Get Request Details
- **Endpoint**: `GET /api/v1/repositories/requests/{id}`
- **Quyền**: `require_user` (Chủ sở hữu hoặc Admin)
- **Response**: `200 OK` (Bao gồm chi tiết phát hiện CVE, masked secrets, coverage và trạng thái).

---

### 1.4. Admin Decision (Exception Approval / Rejection)
- **Endpoint**: `POST /api/v1/repositories/requests/{id}/decision`
- **Quyền**: `require_admin`
- **Request Body**:
  ```json
  {
    "decision": "APPROVED",
    "reason": "Đã kiểm tra CVE mức High và xác nhận không ảnh hưởng môi trường container."
  }
  ```
- **Response**: `200 OK`

---

### 1.5. Deliver Approved Artifact to Workspace
- **Endpoint**: `POST /api/v1/repositories/deliver`
- **Quyền**: `require_user` (Chủ sở hữu workspace hoặc Admin)
- **Request Body**:
  ```json
  {
    "request_id": "req-9a8b7c6d5e4f",
    "target_directory": "/tank/workspaces/researcher/gin-project"
  }
  ```
- **Response**: `200 OK`
  ```json
  {
    "success": true,
    "request_id": "req-9a8b7c6d5e4f",
    "dest_dir": "/tank/workspaces/researcher/gin-project",
    "message": "Phân phối mã nguồn đã xác minh thành công vào workspace"
  }
  ```

---

### 1.6. Audit Trail
- **Endpoint**: `GET /api/v1/repositories/audit?limit=50`
- **Quyền**: `require_admin`
- **Response**: `200 OK` (Danh sách các sự kiện kiểm duyệt và phân phối mã).
