# Security configuration

Require HTTPS, Pangolin authentication, explicit LabOps-Admins grants and MFA where supported. Disable anonymous access, share links and auth bypasses. Keep LabOps login and backend RBAC independent. Store credentials in the root-readable systemd environment file and keep application authentication separate from proxy identity.

Review trusted forwarding headers on Traefik, then check Host, Origin, redirects, CSP and Secure/HttpOnly/SameSite cookies through the public hostname. Use the Next.js BFF for browser API calls. Keep FastAPI, Agent, database, SSH, Docker and WireGuard administration outside Pangolin resources.
