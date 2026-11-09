try:
    import httpx
except ImportError:
    httpx = None

from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class NetdataAdapter:
    """
    Adapter for Netdata Real-time Telemetry Agent
    Fetches real-time system metrics (CPU, RAM, Disk, Net).
    """
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def get_metrics(self) -> Dict[str, Any]:
        if httpx:
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    res = await client.get(f"{self.base_url}/api/v1/info")
                    if res.status_code == 200:
                        data = res.json()
                        return {
                            "status": "AVAILABLE",
                            "cpu_util": data.get("cpu_utilization"),
                            "ram_used_gb": data.get("ram_used_gb"),
                            "uptime": data.get("uptime")
                        }
            except Exception as exc:
                logger.info("Netdata at %s is unavailable: %s", self.base_url, exc)

        return {
            "status": "UNAVAILABLE",
            "cpu_util": None,
            "ram_used_gb": None,
            "uptime": None
        }
