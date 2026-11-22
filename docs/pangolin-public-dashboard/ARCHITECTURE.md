# Architecture

Browser HTTPS → VPS Traefik and Pangolin authorization → Gerbil site tunnel → native Newt on aiot-ws1 → Next.js loopback on 3000 → same-origin BFF → FastAPI loopback on 8000 → Agent loopback on 9100.

The public resource exposes Next.js only. Newt uses the host network namespace to reach the loopback target.
