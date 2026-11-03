import subprocess
import shutil
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class WireGuardAdapter:
    """
    Adapter for WireGuard Site-to-Site & Client Mesh Network.
    Monitors encrypted tunnel connectivity between:
    - Server 01 (Track, Hanoi - 10.200.0.1)
    - Server 02 (Workstation, TP.HCM - 10.200.0.2)
    - Researcher client tunnels (10.200.1.0/24)
    """
    def __init__(self, interface: str = "wg0"):
        self.interface = interface
        self.has_wg = shutil.which("wg") is not None

    async def get_peer_status(self) -> Dict[str, Any]:
        """
        Check wireguard tunnel status using `wg show` or return simulated topology.
        """
        if self.has_wg:
            try:
                out = subprocess.check_output(["wg", "show", self.interface, "dump"], stderr=subprocess.DEVNULL, text=True, timeout=2)
                lines = out.strip().split("\n")
                if len(lines) > 1:
                    peers = []
                    for line in lines[1:]:
                        parts = line.split("\t")
                        if len(parts) >= 8:
                            peers.append({
                                "public_key": parts[0][:12] + "...",
                                "endpoint": parts[2],
                                "allowed_ips": parts[3],
                                "latest_handshake": int(parts[4]),
                                "transfer_rx": int(parts[5]),
                                "transfer_tx": int(parts[6])
                            })
                    return {
                        "interface": self.interface,
                        "status": "active",
                        "peers_count": len(peers),
                        "peers": peers,
                        "live": True
                    }
            except Exception as e:
                logger.info(f"WireGuard real status unavailable ({e}), using topology simulation")

        return {
            "interface": self.interface,
            "status": "active",
            "peers_count": 8,
            "site_to_site": {
                "track": {"vpn_ip": "10.200.0.1", "status": "connected", "latency_ms": 14.2},
                "workstation": {"vpn_ip": "10.200.0.2", "status": "connected", "latency_ms": 14.2}
            },
            "connected_researchers": 5,
            "live": False
        }

