# Test report

Local FastAPI health passed. Docker, SSH and LabOps Agent were active, and wg-easy reported healthy. The Dashboard loopback request failed with connection refused. Static shell syntax checks passed.

| IDs | Tests | Result |
|---|---|---|
| PANG-01–04 | Public DNS, certificate, redirect and connected site | Waiting for infrastructure |
| PANG-05 | Local Dashboard target | Failed |
| PANG-06–14 | Login, authorization, session, API and revocation | Waiting for infrastructure |
| PANG-15–17 | External exposure checks | Not tested externally |
| PANG-18–19 | WireGuard and SSH | Service status only |
| PANG-20–24 | Disconnect, reconnect, recovery and mobile | Not tested |
