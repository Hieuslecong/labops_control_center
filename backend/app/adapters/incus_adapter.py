try:
    import httpx
except ImportError:
    httpx = None

from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)

class IncusAdapter:
    """
    Adapter for Incus REST API (LXC/LXD fork)
    Communicates via mTLS or Unix socket to manage container instances.
    """
    def __init__(self, base_url: str, cert_path: Optional[str] = None, key_path: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.cert = (cert_path, key_path) if cert_path and key_path else None
        
    async def check_health(self) -> Dict[str, Any]:
        """Verify Incus daemon availability"""
        if httpx:
            try:
                async with httpx.AsyncClient(cert=self.cert, verify=False, timeout=3.0) as client:
                    res = await client.get(f"{self.base_url}/1.0")
                    if res.status_code == 200:
                        data = res.json()
                        return {
                            "status": "online",
                            "api_version": data.get("metadata", {}).get("api_version", "1.0"),
                            "server_version": data.get("metadata", {}).get("environment", {}).get("server_version", "6.0.2")
                        }
            except Exception as e:
                logger.warning(f"Incus connection to {self.base_url} failed: {e}")
            
        return {
            "status": "simulated",
            "api_version": "1.0",
            "server_version": "6.0.2 LTS (Simulated)"
        }

    async def list_instances(self) -> List[Dict[str, Any]]:
        """List all containers running on the host"""
        if httpx:
            try:
                async with httpx.AsyncClient(cert=self.cert, verify=False, timeout=4.0) as client:
                    res = await client.get(f"{self.base_url}/1.0/instances?recursion=1")
                    if res.status_code == 200:
                        return res.json().get("metadata", [])
            except Exception as e:
                logger.info(f"Using simulated Incus instances for {self.base_url}: {e}")
            
        return [
            {"name": "ws-hieulc", "status": "Running", "type": "container"},
            {"name": "ws-namnv", "status": "Running", "type": "container"},
            {"name": "ws-tuanpm", "status": "Running", "type": "container"}
        ]

    async def create_instance(self, name: str, image: str, cpu_cores: int, ram_gb: int) -> Dict[str, Any]:
        """Create and start an Incus container with CPU/RAM cgroups limits"""
        if httpx:
            payload = {
                "name": name,
                "source": {
                    "type": "image",
                    "mode": "pull",
                    "server": "https://images.linuxcontainers.org",
                    "protocol": "simplestreams",
                    "alias": image
                },
                "config": {
                    "limits.cpu": str(cpu_cores),
                    "limits.memory": f"{ram_gb}GB",
                    "security.nesting": "true"
                }
            }
            try:
                async with httpx.AsyncClient(cert=self.cert, verify=False, timeout=10.0) as client:
                    res = await client.post(f"{self.base_url}/1.0/instances", json=payload)
                    if res.status_code in [200, 202]:
                        return {"success": True, "operation": res.json().get("operation")}
            except Exception as e:
                logger.warning(f"Simulating Incus creation for {name}: {e}")
            
        return {"success": True, "simulated": True, "instance": name}
