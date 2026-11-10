try:
    import httpx
except ImportError:
    httpx = None

import shutil
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)

class IncusAdapter:
    """
    Adapter for Incus REST API (LXC/LXD fork).
    Communicates via mTLS or Unix socket to manage container instances.
    In Production Mode (demo_mode=False), fails closed and reports true system status.
    """
    def __init__(
        self,
        base_url: str,
        cert_path: Optional[str] = None,
        key_path: Optional[str] = None,
        demo_mode: bool = True
    ):
        self.base_url = base_url.rstrip("/")
        self.cert = (cert_path, key_path) if cert_path and key_path else None
        self.demo_mode = demo_mode
        self.has_incus_cli = shutil.which("incus") is not None
        
    async def check_health(self) -> Dict[str, Any]:
        """Verify Incus daemon availability"""
        if self.demo_mode:
            return {"status": "simulated", "api_version": "N/A", "server_version": "DEMO", "is_configured": False}
        if httpx and self.base_url.startswith("http"):
            try:
                async with httpx.AsyncClient(cert=self.cert, timeout=3.0) as client:
                    res = await client.get(f"{self.base_url}/1.0")
                    if res.status_code == 200:
                        data = res.json()
                        return {
                            "status": "online",
                            "api_version": data.get("metadata", {}).get("api_version", "1.0"),
                            "server_version": data.get("metadata", {}).get("environment", {}).get("server_version", "6.0.2"),
                            "is_configured": True
                        }
            except Exception as e:
                logger.warning(f"Incus connection to {self.base_url} failed: {e}")

        return {
            "status": "not_configured",
            "api_version": "N/A",
            "server_version": "Chưa kết nối",
            "is_configured": False,
            "message": "Incus daemon không truy cập được hoặc TLS chưa được xác minh"
        }

    async def list_instances(self) -> List[Dict[str, Any]]:
        """List all containers running on the host"""
        if self.demo_mode:
            return [
                {"name": "ws-demo-01", "status": "Running", "type": "container", "demo": True},
                {"name": "ws-demo-02", "status": "Running", "type": "container", "demo": True},
            ]
        if httpx and self.base_url.startswith("http"):
            try:
                async with httpx.AsyncClient(cert=self.cert, timeout=4.0) as client:
                    res = await client.get(f"{self.base_url}/1.0/instances?recursion=1")
                    if res.status_code == 200:
                        return res.json().get("metadata", [])
            except Exception as e:
                logger.info(f"Failed to fetch Incus instances from {self.base_url}: {e}")
            
        return []

    async def create_instance(
        self,
        name: str,
        image: str,
        cpu_cores: int,
        ram_gb: int,
        isolated: bool = True
    ) -> Dict[str, Any]:
        """Create and start an Incus container with CPU/RAM cgroups limits and isolated user idmap"""
        config = {
            "limits.cpu": str(cpu_cores),
            "limits.memory": f"{ram_gb}GB",
            "security.nesting": "false",
            "security.idmap.isolated": "true" if isolated else "false"
        }
        if self.demo_mode:
            return {"success": True, "simulated": True, "instance": name, "config": config}
        if httpx and self.base_url.startswith("http"):
            payload = {
                "name": name,
                "source": {
                    "type": "image",
                    "mode": "pull",
                    "server": "https://images.linuxcontainers.org",
                    "protocol": "simplestreams",
                    "alias": image
                },
                "config": config
            }
            try:
                async with httpx.AsyncClient(cert=self.cert, timeout=10.0) as client:
                    res = await client.post(f"{self.base_url}/1.0/instances", json=payload)
                    if res.status_code in [200, 202]:
                        return {"success": True, "operation": res.json().get("operation")}
            except Exception as e:
                logger.warning(f"Incus creation failed for {name}: {e}")

        return {"success": False, "error": "Incus operation failed or could not be verified"}

    async def list_storage_pools(self) -> List[Dict[str, Any]]:
        """List storage pools on this host"""
        if self.demo_mode:
            return [{"name": "pool-demo", "driver": "dir", "status": "DEMO", "demo": True}]
        if httpx and self.base_url.startswith("http"):
            try:
                async with httpx.AsyncClient(cert=self.cert, timeout=4.0) as client:
                    res = await client.get(f"{self.base_url}/1.0/storage-pools?recursion=1")
                    if res.status_code == 200:
                        return res.json().get("metadata", [])
            except Exception as exc:
                logger.warning("Incus storage pool query failed: %s", exc)
        return []

    async def create_storage_volume(self, pool_name: str, volume_name: str, size_gb: int) -> Dict[str, Any]:
        """Create custom storage volume with strict quota"""
        if self.demo_mode:
            return {"success": True, "simulated": True, "volume": volume_name, "pool": pool_name, "size_gb": size_gb}
        payload = {
            "name": volume_name,
            "type": "custom",
            "config": {
                "size": f"{size_gb}GiB"
            }
        }
        if httpx and self.base_url.startswith("http"):
            try:
                async with httpx.AsyncClient(cert=self.cert, timeout=10.0) as client:
                    res = await client.post(f"{self.base_url}/1.0/storage-pools/{pool_name}/volumes/custom", json=payload)
                    if res.status_code in [200, 201, 202]:
                        return {"success": True, "volume": volume_name, "pool": pool_name, "size_gb": size_gb}
            except Exception as e:
                logger.warning(f"Incus volume creation failed for {volume_name}: {e}")

        return {"success": False, "error": "Incus volume creation failed or could not be verified"}

    async def attach_storage_volume(self, instance_name: str, pool_name: str, volume_name: str, mount_path: str) -> Dict[str, Any]:
        """Attach custom storage volume to container at approved mount path (e.g. /workspace)"""
        device_spec = {
            "type": "disk",
            "pool": pool_name,
            "source": volume_name,
            "path": mount_path
        }
        if self.demo_mode:
            return {"success": True, "simulated": True, "instance": instance_name, "device": device_spec}
        if httpx and self.base_url.startswith("http"):
            try:
                async with httpx.AsyncClient(cert=self.cert, timeout=10.0) as client:
                    res = await client.patch(
                        f"{self.base_url}/1.0/instances/{instance_name}",
                        json={"devices": {f"disk-{volume_name}": device_spec}}
                    )
                    if res.status_code in [200, 202]:
                        return {"success": True, "attached_device": device_spec}
            except Exception as e:
                logger.warning(f"Incus attach failed for {instance_name}: {e}")

        return {"success": False, "error": "Incus volume attachment failed or could not be verified"}

    async def add_proxy_device(self, instance_name: str, device_name: str, listen_ip: str, listen_port: int, connect_port: int = 22) -> Dict[str, Any]:
        raise RuntimeError("Per-user host SSH proxy ports are disabled")

    async def remove_proxy_device(self, instance_name: str, device_name: str) -> Dict[str, Any]:
        raise RuntimeError("Legacy SSH proxy removal cannot be verified through this adapter")

    async def verify_privilege_isolation(self, instance_name: str, username: str) -> Dict[str, Any]:
        raise RuntimeError("Workspace privilege isolation has not been checked on the remote instance")

    async def configure_sshd_dual_auth(
        self,
        instance_name: str,
        username: str,
        public_key: Optional[str] = None,
        password_hash: Optional[str] = None
    ) -> Dict[str, Any]:
        raise RuntimeError("Per-user password SSH configuration is disabled; use Teleport certificates")

    async def verify_dual_auth_policy(
        self,
        instance_name: str,
        username: str,
        host_ip: str = "127.0.0.1"
    ) -> Dict[str, Any]:
        raise RuntimeError("SSH policy verification requires live Teleport and workspace probes")
