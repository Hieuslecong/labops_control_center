# LabOps Security Assessment & Verification Checklist

**Date:** 2026-11-17T15:43:00+07:00  
**Host Target:** `aiot-ws1`  
**Security Level:** Non-Disruptive, Strict Least-Privilege  

---

## 1. Non-Disruptive Policy Verification

| Check Item | Status | Verification Detail |
| :--- | :--- | :--- |
| **Network Interfaces & Subnets** | **PASS** | No changes to IP, DNS, gateway, NetworkManager, or routes. |
| **VPN & WireGuard** | **PASS** | No changes to WireGuard tunnels, wg-easy, or iptables. |
| **NVIDIA Driver & GPU** | **PASS** | `nvidia-smi` intact. Active user workload (23GB VRAM) untouched. |
| **Docker Daemon & Containers** | **PASS** | No docker prune, down -v, or container removal executed. |
| **Storage & Mounts** | **PASS** | Disks `sda`, `sdb`, `sdc`, `sdd`, `sde` untouched. Zero formatting. |
| **User Accounts & Privileges** | **PASS** | No user deletion or sudo modifications. Operations run as `aiot-ws1`. |

---

## 2. AI Assistant & Zalo Security Controls

| Security Control | Implementation | Verification Status |
| :--- | :--- | :--- |
| **Single Polling Consumer** | Lease checking and token validation in `ZaloBotEngine` | **ENFORCED** — Prevents duplicate consumer with rack |
| **Read-Only Tools Only** | Only 6 read tools enabled. 7 destructive write tools disabled | **ENFORCED** — Shell, sudo, reboot, delete tools blocked |
| **Monthly Quota Ledger** | Atomic reservation in `QuotaLedger` (2.800 standard / 200 alert) | **ENFORCED** — Strict ceiling of 3.000 outbound msgs/month |
| **Docker Socket Isolation** | Bot process has zero access/mount to `/var/run/docker.sock` | **ENFORCED** |
| **Token Secret Protection** | Masked tokens in UI/API; secrets stored only via env/keyfiles | **ENFORCED** — No plaintext secrets committed or displayed |

---

## 3. 9Router AI Gateway Security

| Security Control | Implementation | Verification Status |
| :--- | :--- | :--- |
| **Network Binding** | Bound strictly to loopback `127.0.0.1:20128` | **VERIFIED** |
| **No Public Port Exposure** | Port 20128 blocked from external WAN / router exposure | **VERIFIED** |
| **Authentication** | Bearer API token resolved securely from local SQLite store | **VERIFIED** (`PONG` test passed) |
| **Model Verification** | User-designated model `model_free` active and verified | **VERIFIED** |

---

## 4. Web Dashboard & Authentication Security

| Security Control | Implementation | Verification Status |
| :--- | :--- | :--- |
| **Session Integrity** | HMAC-SHA256 session cookie (`labops_session`) with 7-day TTL | **VERIFIED** |
| **Password Verification** | Constant-time `timingSafeEqual` comparison against `LABOPS_ADMIN_PASSWORD` | **VERIFIED** |
| **RBAC Enforcement** | Separation of `ADMIN` (7 core groups) vs `RESEARCHER` (4 isolated groups) | **VERIFIED** |
| **Fail-Closed API Proxy** | Missing secrets return HTTP 503; unauthorized requests return HTTP 401 | **VERIFIED** |
