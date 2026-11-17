# Network immutability and production safety review

**Snapshot:** 2026-11-17

## Actions performed

- Source edits, test/build commands, Git reads and temporary-file tests were limited to the development clone, /tmp, the isolated Python virtualenv and Go cache.
- No command or test changed IP addressing, NetworkManager, interfaces, routes, DNS, firewall/NAT, MikroTik, WireGuard/wg-easy runtime, SSH configuration or host Docker networking.
- No reboot/shutdown, production service restart, deployment, production database migration, production container stop, process kill, user-data deletion or GPU operation was performed.
- Go PKI/Gateway integration tests briefly opened ephemeral 127.0.0.1 listeners using test CA/certificates, temporary state and no non-loopback interface.
- The final Next.js build fetched the app-declared Google Fonts after an approved network-access escalation. This was an outbound dependency fetch by the build process; it did not change host network configuration. Sentry release upload did not occur because no auth token was supplied.

## Evidence boundaries

- pwd -P and Git root resolved to /home/aiot-1/labops-v2-hardening. The production repository was not written.
- Docker CLI is present at /usr/bin/docker, but no Docker command/API/socket was used. No Docker daemon or rootless test daemon was started.
- psql is present; postgres and pg_ctl were not found on PATH. No database endpoint was contacted.
- No network state was measured. These facts establish that this work performed no network mutation; they do not establish current Rack/Workstation IP, route, DNS, firewall or service state.
- Host-wide mount/service monitoring was not available as evidence. This report does not claim full clone isolation from every external process.
- No egress enforcement test was run in a network namespace/container. Host firewall, DNS and routing were not modified.

## Final policy check

No changes were made to Rack IP 192.168.167.250, Workstation IP 192.168.167.251, interfaces eno2/eno1/enp5s0, profiles, routes, DNS, firewall/NAT, MikroTik, WireGuard/wg-easy runtime, SSH ports 1911/1912, or reboot state. These are statements about actions taken, not measurements of the live host. Repository egress enforcement is HOST_ENFORCEMENT_NOT_VERIFIED and host application is blocked by the immutable infrastructure policy.
