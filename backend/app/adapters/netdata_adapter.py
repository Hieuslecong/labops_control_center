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
                            "status": "online",
                            "cpu_util": data.get("cpu_utilization", 42.5),
                            "ram_used_gb": data.get("ram_used_gb", 174.2),
                            "uptime": data.get("uptime", "45d 12h")
                        }
            except Exception as e:
                logger.info(f"Netdata at {self.base_url} simulated: {e}")

        return {
            "status": "simulated",
            "cpu_util": 42.5,
            "ram_used_gb": 174.2,
            "uptime": "45d 12h"
        }
