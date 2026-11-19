# LabOps WireGuard VPN Recovery Report — Legacy Setup Comparison & Runtime Fix

**Workstation Host:** `aiot-ws1` (`192.168.167.251`, `enp5s0`)  
**Legacy Reference:** Branch `feature/labops-control-center` @ commit `9b1d8e7`  
**Target Branch:** `feature/storage-resource-allocation` (base `087db79` + recovery & hardening changes)  
**Gateway Container:** `labops-wg-easy` (`ghcr.io/wg-easy/wg-easy:15`, Web API `127.0.0.1:51821`, UDP `2204`)

---

## 1. Status Classification (Honest & Evidence-Based)

| Category | Verdict | Evidence & Remaining Boundary |
|---|---|---|
| **SOURCE_FIXED** | `PASS` | Infrastructure scripts/compose, backend adapters/services, frontend diagnostics, and regression tests updated and verified (`pytest`: 66 passed; `tsc --noEmit` & `oxlint`: 0 errors, 0 warnings on edited files; `bash -n`: exit 0). |
| **RUNTIME_FIXED** | `PARTIAL_API_APPLIED` | Applied & persisted via official `wg-easy v15.5.0` HTTP API (`10.88.40.0/24`, UDP `2204`, scoped IPv4 NAT, `1911`/`1912` DROP hooks, 3x `POST /api/admin/interface/restart`); local OpenSSH banners verified on `192.168.167.251:22004` and `:1912`. Direct kernel inspection inside `labops-wg-easy` (`wg show`, `ip addr`, `iptables`) requires `sudo` or `docker` group permission unavailable to `aiot-ws1`. |
| **E2E_VERIFIED** | `E2E_CLIENT_VERIFICATION_REQUIRED` | MacBook (`10.88.40.2`) reconnection, fresh handshake, `tcpdump` return-path check, and real `ssh labops-testacc2` must be executed by the operator after restarting/reconnecting the WireGuard tunnel. |
| **SECURITY_VERIFIED** | `PASS (Source & API Rules) / PENDING_SUDO_KERNEL_AUDIT` | Removed hardcoded fallback password `LabOpsSecureVPN2026!` and legacy `:2205` fallback; removed fake unregistered `.conf` generation; Researcher SSH ACL blocks TCP `1911`/`1912` in resolver and `wg0` hooks (kernel `iptables -S FORWARD` confirmation pending `sudo`). |

---

## 2. Phase 1 Audit Matrix & Root Cause Analysis

### 2.1 Configuration vs Persisted vs Runtime Matrix

| Layer / Item | Configured (`compose.yaml` / `.env`) | Persisted (`wg-easy` API / DB) | Kernel / Container Runtime (Observed Before Fix vs After API Fix) | Expected | Verdict |
|---|---|---|---|---|---|
| **WG UDP Port** | Legacy `9b1d8e7`: dual `2204` + `2205`; `087db79`: `${WG_EASY_UDP_PORT:-2205}`; Fixed: `${WG_EASY_UDP_PORT:-2204}` | `2204` (`interface.port`, `userconfig.port`) | Host listener active on `0.0.0.0:2204/udp` | `2204/udp` | Aligned |
| **IPv4 VPN CIDR** | `10.88.40.0/24` | `10.88.40.0/24` | Before fix: container `wg0` runtime stayed on default `10.8.0.1/24` + route `10.8.0.0/24` + NAT `10.8.0.0/24`. After API fix: 3x `POST /api/admin/interface/restart` executed with `10.88.40.0/24` + scoped hooks (`sudo` kernel check pending). | `10.88.40.0/24` (`wg0` gateway `10.88.40.1/24`) | API fixed; `sudo` kernel verify required |
| **IPv6 CIDR** | Missing `INIT_IPV6_CIDR` in `9b1d8e7`/`087db79`; added in fixed `compose.yaml` | `fdcc:ad94:bacf:61a4::cafe:0/112` | Preserved across `POST /api/admin/hooks` and `ensure_subnet_configured()` | Preserve `fdcc:ad94:bacf:61a4::cafe:0/112` | Aligned |
| **Public Endpoint (`userconfig`)** | Legacy `compose.yaml` mixed `WG_EASY_PUBLIC_ENDPOINT` (`host:port`) into `INIT_HOST`; split cleanly to `WG_EASY_PUBLIC_ENDPOINT_HOST=118.70.169.236` + `WG_EASY_UDP_PORT=2204` | `host: "118.70.169.236"`, `port: 2204` | `118.70.169.236:2204` | `118.70.169.236:2204` | Aligned |
| **Peer #1 (`testacc2`)** | Preserved existing DB peer (not deleted) | `id: 1`, `name: "testacc2-Thiết bị chính"`, `ipv4Address: "10.88.40.2"`, `enabled: true` | Prior handshake `2026-11-19T07:35:37.000Z` from `210.245.52.155:62114`; reset to `latestHandshakeAt: null` (`rx=0, tx=0`) after controlled interface restarts | Preserved on `10.88.40.2`; await client reconnect | Intact (reconnect needed) |
| **SSH Ports on `192.168.167.251`** | Host `sshd` admin `1912`, workspace `22004` | N/A | Live TCP connect verified: `SSH-2.0-OpenSSH_10.0p2 Debian-7+deb13u4` on `:22004` and `:1912` | `:22004` Workspace reachable; `:1911`/`:1912` blocked from VPN tunnel | OpenSSH up; ACL hooks applied |

### 2.2 Confirmed Root Cause

1. **`INIT_*` bootstrap & CIDR sync without interface restart:** `wg-easy` v15 only applies `INIT_*` variables on first database creation (`wg_easy_data` empty). When `wg0` booted initially on `10.8.0.1/24` (or `ensure_subnet_configured()` later updated persisted DB CIDR via `POST /api/admin/interface/cidr` during `create_device()` without calling `POST /api/admin/interface/restart` or updating firewall hooks), the kernel `wg0` interface, route table, and `iptables` NAT `POSTROUTING` inside the container remained on `10.8.0.0/24`. Meanwhile peer #1 (`testacc2`) held `10.88.40.2`. Inbound SYN packets from `10.88.40.2` arrived on Workstation, and return SYN-ACK packets missed container `wg0` routing and leaked out `enp5s0`, causing MacBook SSH connections to `192.168.167.251:22004` and `:1912` to time out.
2. **Backend/Compose gaps across both branches:**
   - Missing `INIT_IPV6_CIDR` in `compose.yaml`.
   - `WG_EASY_ADMIN_PASSWORD` fell back to hardcoded `"LabOpsSecureVPN2026!"` in `compose.yaml` and `backend/app/core/config.py`.
   - `WgEasyAdapter.get_connection_status()` previously reported `HEALTHY` solely from HTTP API reachability without inspecting kernel `wg0`, routes, or NAT.
   - `labops_service.py` and `ssh_access_manager.py` still allowed fallback local `.conf` generation (`118.70.169.236:2205`) even when no peer existed on `wg-easy`.

---

## 3. Legacy (`9b1d8e7`) vs Current (`087db79` + Recovery Fixes)

| Component | Legacy `feature/labops-control-center` (`9b1d8e7`) | Pre-Fix `087db79` | Final State (`feature/storage-resource-allocation` + Fixes) | Decision |
|---|---|---|---|---|
| `infrastructure/wg-easy/compose.yaml` | Dual UDP `2204` + `2205`; hardcoded `LabOpsSecureVPN2026!`; `INIT_PORT=2205`; mixed `WG_EASY_PUBLIC_ENDPOINT`; no `INIT_IPV6_CIDR`. | Single UDP `${WG_EASY_UDP_PORT:-2205}`; kept hardcoded password & missing `INIT_IPV6_CIDR`. | Single UDP `${WG_EASY_UDP_PORT:-2204}`; mandatory `${WG_EASY_ADMIN_PASSWORD:?...}`; split `WG_EASY_PUBLIC_ENDPOINT_HOST`; added `INIT_IPV6_CIDR`. | Kept single-gateway loopback-UI architecture; eliminated dual-run `2205` port and hardcoded secrets. |
| `infrastructure/wg-easy/.env.example` | Contained legacy defaults aligned with `2205`. | Template existed with `LabOpsSecureVPN2026!` / mixed vars. | Clean placeholder template with `WG_EASY_UDP_PORT=2204`, `WG_EASY_PUBLIC_ENDPOINT_HOST=118.70.169.236`, `WG_EASY_VPN_SUBNET=10.88.40.0/24`, `WG_EASY_VPN_IPV6_SUBNET`. | Replaced secret defaults with explicit placeholders. |
| `.env.workstation.example` | Legacy mixed endpoint / pubkey env. | Included `LabOpsSecureVPN2026!` and static pubkey note. | Removed hardcoded password; set `WG_EASY_PUBLIC_ENDPOINT=118.70.169.236:2204`, `WG_EASY_UDP_PORT=2204`, runtime pubkey note. | Kept server role env structure; eliminated secret defaults. |
| `infrastructure/wg-easy/backup.sh` | Basic volume tarball backup. | Unchanged from legacy. | Added hot `sqlite3 .backup` snapshot when container is running + read-only Docker volume tarball + `tar -tzf` integrity verification. | Upgraded cleanly without touching live volume. |
| `infrastructure/wg-easy/healthcheck.sh` | Checked container, port 51821, basic `wg show`. | Unchanged from legacy. | Added explicit runtime `wg0` IPv4 CIDR (`10.88.40.1/24`), kernel route (`10.88.40.0/24`), and `iptables -t nat -S POSTROUTING` verification (`VPN_INTERFACE_MISMATCH`, `VPN_ROUTE_MISMATCH`, `VPN_NAT_MISMATCH`). | Kept non-destructive CLI design; upgraded checks for routing parity. |
| `backend/app/adapters/wg_easy_adapter.py` | Basic CRUD + implicit CIDR call without restart. | Added `detect_routing_inconsistencies()` basics, but kept implicit CIDR call in `create_device()` and returned `HEALTHY` without kernel proof. | Added `restart_interface()`, `get_hooks()`, `update_hooks()`, `get_userconfig()`, `inspect_runtime_network()`; removed CIDR mutation side effect from `create_device()`; preserved IPv6 + optional restart in `ensure_subnet_configured()`; reports `VPN_API_HEALTHY` vs `HEALTHY` vs `VPN_ROUTING_MISCONFIGURED`. | Eliminated fake `HEALTHY` & implicit mid-provisioning CIDR drift. |
| `backend/app/services/vpn_route_resolver.py` & `wireguard_config_builder.py` | Not yet split cleanly (older static generation). | Introduced dynamic host isolation (`192.168.167.251` vs `.250`) and Researcher port clamping. | Added `host_online` (`HOST_OFFLINE_UNVERIFIED`), enforced `VPN_ROUTING_MISCONFIGURED`, `PEER_NOT_REGISTERED_ON_GATEWAY`, `PEER_IP_OUTSIDE_SUBNET`, `GATEWAY_ENDPOINT_UNVERIFIED`, and dynamic `effective_cidr`. | Kept multi-host separation architecture; enforced strict fail-closed validation. |
| `backend/app/services/labops_service.py` & `ssh_access_manager.py` | Fallback local WireGuard `.conf` generation. | Kept fallback fake `.conf` with `:2205` in ZIP/bundle paths. | Removed fallback local `.conf` generation completely; unprovisioned/offline peers receive `wireguard/WIREGUARD_PENDING.md` instead of `labops.conf`. | Eliminated drift-prone unregistered `.conf` files (`:2205`). |

---

## 4. Safe Backup & Rollback Plan

### 4.1 Backup Created Before Runtime Modification
Directory `/tmp/labops-wg-backup-20261119/` (`chmod 700`, sensitive JSON files `chmod 600`):
- `interface.json` (`868 B`), `hooks.json` (`1.1 K`), `general.json` (`109 B`), `userconfig.json` (`456 B`), `information.json` (`265 B`), `clients.json` (`1.2 K`)
- `compose.yaml.bak` (`1.6 K`)
- `host_network_before.txt` (`6.0 K`)

### 4.2 Rollback Procedure (If Runtime Regression Occurs)
1. **Restore hooks & CIDR via `wg-easy` API** using values from `/tmp/labops-wg-backup-20261119/hooks.json` and `interface.json`, followed immediately by `POST /api/admin/interface/restart`:
   ```bash
   set -a && source .env.local && set +a
   curl -fsS -u "${WG_EASY_ADMIN_USER}:${WG_EASY_ADMIN_PASSWORD}" \
     -H "Content-Type: application/json" \
     -X POST "http://127.0.0.1:51821/api/admin/hooks" \
     --data-binary @/tmp/labops-wg-backup-20261119/hooks.json
   curl -fsS -u "${WG_EASY_ADMIN_USER}:${WG_EASY_ADMIN_PASSWORD}" \
     -H "Content-Type: application/json" \
     -X POST "http://127.0.0.1:51821/api/admin/interface/restart" -d '{}'
   ```
2. **Full volume backup before any compose change** (`sudo` / `docker` group required):
   ```bash
   ./infrastructure/wg-easy/backup.sh
   ```
3. **Zero peers deleted:** Peer `#1` (`testacc2-Thiết bị chính`, `10.88.40.2`) was preserved untouched in SQLite. Once stable MacBook SSH connectivity via VPN is confirmed, rotate peer #1's keypair cleanly via LabOps UI / `wg-easy` API as post-incident hygiene because temporary client credentials were shared in terminal logs during earlier troubleshooting.

---

## 5. Runtime Recovery Applied & Live Evidence

Via authenticated requests to the running `wg-easy v15.5.0` API (`http://127.0.0.1:51821`):
1. Scoped `POST /api/admin/hooks` (`id: "wg0"`):
   - Cleans up legacy NAT rules (`-s 10.8.0.0/24 -o {{device}} -j MASQUERADE`, `-s {{ipv4Cidr}} -o {{device}} -j MASQUERADE`) and broad `FORWARD -i {{interface}} -j ACCEPT`.
   - Applies scoped IPv4 NAT: `iptables -t nat -A POSTROUTING -s {{ipv4Cidr}} -d 192.168.167.250/31 -o {{device}} -j MASQUERADE`.
   - Applies Researcher host-admin SSH isolation: drops `FORWARD -i {{interface}} -p tcp --dport 1911 -j DROP` and `--dport 1912 -j DROP` (both `iptables` and `ip6tables`).
   - Permits WireGuard tunnel forward to Lab hosts on workspace ports (`22000:29999`), port `22`, and `icmp` within `192.168.167.250/31`, drops remaining `FORWARD -i {{interface}}`, and permits return traffic `-o {{interface}} -j ACCEPT`.
2. Restarted `wg0` via `POST /api/admin/interface/restart` (`{"success": true}` across 3 restarts).
3. Final read-only API + SSH socket checks confirmed:
   - `GET http://127.0.0.1:51821/login` -> `200`
   - `GET /api/admin/interface` -> `{"name": "wg0", "enabled": true, "ipv4Cidr": "10.88.40.0/24", "ipv6Cidr": "fdcc:ad94:bacf:61a4::cafe:0/112", "port": 2204, "device": "eth0", "publicKey": "TA7sUCFRsPHfg52B/Gh4w7vnu3SpzaxH4A1HS6N3bDk="}`
   - `GET /api/admin/userconfig` -> `{"host": "118.70.169.236", "port": 2204, "dns": null}`
   - `GET /api/client` -> `[{"id": 1, "name": "testacc2-Thiết bị chính", "ipv4Address": "10.88.40.2", "enabled": true, "latestHandshakeAt": null, "transferRx": 0, "transferTx": 0}]`
   - Local TCP connects to `192.168.167.251:22004` and `:1912` both returned `SSH-2.0-OpenSSH_10.0p2 Debian-7+deb13u4`.

---

## 6. Architecture Summary: Multi-Host Routing & Isolation

- **Workstation (`aiot-ws1`, `192.168.167.251`):** Primary `labops-wg-easy` gateway (`UDP 2204`, `10.88.40.0/24`). Workstation users receive `AllowedIPs = 10.88.40.0/24, 192.168.167.251/32` and workspace SSH ports `22000–29999` (e.g., `22004`).
- **Rack (`aiotlab-rack`, `192.168.167.250`):** Unmodified during this recovery; served via the Workstation gateway over LAN `192.168.167.250/31` NAT scope. Rack users receive `AllowedIPs = 10.88.40.0/24, 192.168.167.250/32` without Workstation LAN route exposure.
- **Role Enforcement:** Admin SSH ports `1911` (Rack) and `1912` (Workstation) are blocked at both `VPNRouteResolver` (clamped away for `RESEARCHER`) and `wg0` firewall hooks (`TCP 1911/1912 DROP` on `-i {{interface}}`).

---

## 7. Automated Verification Results

All checks below ran after the final edit to each corresponding file:

| Check Command | Scope | Result |
|---|---|---|
| `bash -n infrastructure/wg-easy/backup.sh infrastructure/wg-easy/healthcheck.sh` | Shell syntax for backup & healthcheck scripts | Exit `0` |
| `PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_vpn_routing_recovery.py` | 9 new VPN recovery, mismatch blocking, fail-closed & hygiene tests | `9 passed in 1.77s` |
| `PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_vpn_routing_recovery.py backend/tests/test_vpn_unification.py backend/tests/test_v2_vpn_wgeasy.py backend/tests/test_admin_ssh_lifecycle.py backend/tests/test_member_provisioning.py backend/tests/test_storage_and_shared_resources.py backend/tests/test_download_zip_and_safe_delete.py` | Full VPN, provisioning, lifecycle & security regression suites | `66 passed, 1 warning in 51.99s` (warning from upstream `fastapi.testclient` `StarletteDeprecationWarning`) |
| `bun run typecheck && bun run lint` | TypeScript 7 strict typecheck + `oxlint` + `lint:deprecated` | Exit `0` (`tsc`: 0 errors; `oxlint`: 0 errors, 23 pre-existing warnings in untouched files; `lint:deprecated`: 0 warnings, 0 errors) |
| `./node_modules/.bin/oxlint src/features/users/components/member-management-view.tsx src/features/users/components/member-provisioning-wizard.tsx src/types/labops.ts` | Modified frontend files only | `0 warnings, 0 errors` |

---

## 8. Operator Verification Runbook (`sudo` Kernel + MacBook E2E)

Because user `aiot-ws1` is not in the `docker` group and `sudo` requires interactive authentication, run the commands below to promote `RUNTIME_FIXED` and `E2E_VERIFIED` to full pass:

### 8.1 On Workstation `aiot-ws1` (Kernel & Firewall Inspection)

```bash
cd /home/aiot-ws1/projects/labops_control_center

# 1. Chạy script healthcheck chính thức (kiểm tra wg0 IP 10.88.40.1/24, route, NAT POSTROUTING)
sudo -E ./infrastructure/wg-easy/healthcheck.sh

# 2. Kiểm tra trực tiếp kernel WireGuard, route & iptables FORWARD (xác nhận DROP 1911/1912)
sudo docker exec labops-wg-easy sh -c '
  ip -4 addr show dev wg0 &&
  ip -4 route show dev wg0 &&
  iptables -t nat -S POSTROUTING &&
  iptables -S FORWARD &&
  wg show wg0
'

# 3. Tạo bản sao lưu volume vật lý nhất quán trước khi hoàn tất
sudo ./infrastructure/wg-easy/backup.sh
```

### 8.2 Packet Capture During Live MacBook Test (Terminal 2 on `aiot-ws1`)

```bash
# Theo dõi gói ICMP & TCP 22004 giữa client WireGuard 10.88.40.2 và 192.168.167.251
sudo tcpdump -i any -nn "(host 10.88.40.2 or udp port 2204 or tcp port 22004)"
```

### 8.3 On MacBook (`10.88.40.2` — End-to-End Verification)

1. Turn WireGuard tunnel `labops` **OFF** and back **ON** so a fresh handshake completes with `118.70.169.236:2204`.
2. Run E2E checks:
   ```bash
   # Kiểm tra route đi qua tunnel WireGuard tới gateway và host
   ping -c 3 10.88.40.1
   nc -vz -w 5 192.168.167.251 22004
   # Kỳ vọng connection timeout/refused (bị DROP bởi ACL từ VPN):
   nc -vz -w 3 192.168.167.251 1912
   # Kết nối SSH workspace thực tế:
   ssh -v labops-testacc2
   ```
3. Once stable SSH login is confirmed, rotate peer #1's WireGuard keypair and reissue `LabOps_Access_testacc2.zip` as routine security hygiene.
