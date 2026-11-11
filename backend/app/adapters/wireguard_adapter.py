import base64
import fcntl
import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional


class WireGuardError(RuntimeError):
    """WireGuard is unavailable or could not verify the requested change."""


class WireGuardAdapter:
    def __init__(
        self,
        interface: str = "wg0",
        data_dir: Optional[str] = None,
        server_configs: Optional[Dict[str, Dict[str, Any]]] = None,
        local_host_id: Optional[str] = None,
    ):
        self.interface = interface
        self.local_host_id = local_host_id or os.getenv("LOCAL_HOST_ID", "workstation")
        self.has_wg = shutil.which("wg") is not None
        self.data_dir = data_dir or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data")
        )
        raw_config = os.getenv("WIREGUARD_SERVERS_JSON", "")
        try:
            self.server_configs = server_configs if server_configs is not None else json.loads(raw_config or "{}")
        except json.JSONDecodeError as exc:
            raise WireGuardError("WIREGUARD_SERVERS_JSON is invalid") from exc

        # Tự động nạp cấu hình Rack server chính thức nếu chưa có trong biến môi trường
        rack_pub = os.getenv("LABOPS_WG_SERVER_PUBLIC_KEY", "jf+Uw/DFyi5swclp7mhH9mamYjQgV3ulZRlxBIdqlmQ=")
        rack_endpoint = os.getenv("LABOPS_WG_ENDPOINT", "118.70.169.236:2204")
        if "track" not in self.server_configs:
            self.server_configs["track"] = {
                "server_public_key": rack_pub,
                "server_endpoint": rack_endpoint,
                "vpn_subnet": "10.88.30.0/24",
                "server_vpn_ip": "10.88.30.1",
                "client_allowed_ips": ["10.88.30.1/32", "192.168.167.250/32"],
                "interface": self.interface,
            }
        self.peers_file = os.path.join(self.data_dir, "wireguard_peers.json")
        self.lock_file = os.path.join(self.data_dir, "wireguard_peers.lock")
        self.peers: Dict[str, Dict[str, Any]] = self._load_peers()

    @staticmethod
    def is_valid_key(key: Optional[str]) -> bool:
        if not key or not isinstance(key, str):
            return False
        try:
            raw = base64.b64decode(key, validate=True)
            return len(raw) == 32 and raw != bytes(32) and base64.b64encode(raw).decode("ascii") == key
        except Exception:
            return False

    def _load_peers(self) -> Dict[str, Dict[str, Any]]:
        try:
            with open(self.peers_file, "r", encoding="utf-8") as source:
                records = json.load(source)
            return records if isinstance(records, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise WireGuardError("Could not read WireGuard peer records") from exc

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        os.makedirs(self.data_dir, mode=0o700, exist_ok=True)
        with open(self.lock_file, "a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.peers = self._load_peers()
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _save_peers(self) -> None:
        os.makedirs(self.data_dir, mode=0o700, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix="wireguard-peers-", dir=self.data_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                os.fchmod(target.fileno(), 0o600)
                json.dump(self.peers, target, ensure_ascii=False, indent=2)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temp_path, self.peers_file)
        except OSError as exc:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise WireGuardError("Could not persist WireGuard peer records") from exc

    def _server(self, server_id: str) -> Dict[str, Any]:
        cfg = self.server_configs.get(server_id)
        if not isinstance(cfg, dict):
            raise WireGuardError(f"WireGuard is not configured for server '{server_id}'")
        public_key = cfg.get("server_public_key")
        if not self.is_valid_key(public_key):
            raise WireGuardError(f"WireGuard server public key is invalid for '{server_id}'")
        endpoint = cfg.get("server_endpoint", "")
        match = re.fullmatch(r"(?:\[[0-9a-fA-F:]+\]|[A-Za-z0-9.-]+):([0-9]{1,5})", endpoint)
        if not match or not 1 <= int(match.group(1)) <= 65535:
            raise WireGuardError(f"WireGuard endpoint is invalid for '{server_id}'")
        try:
            network = ipaddress.ip_network(cfg["vpn_subnet"], strict=True)
            server_ip = ipaddress.ip_address(cfg["server_vpn_ip"])
        except (KeyError, ValueError) as exc:
            raise WireGuardError(f"WireGuard VPN subnet is invalid for '{server_id}'") from exc
        if server_ip not in network:
            raise WireGuardError(f"WireGuard gateway address is outside the subnet for '{server_id}'")
        client_ips = cfg.get("client_allowed_ips")
        if isinstance(client_ips, str):
            client_ips = [item.strip() for item in client_ips.split(",") if item.strip()]
        if not isinstance(client_ips, list) or not client_ips:
            raise WireGuardError(f"Teleport Proxy route is not configured for '{server_id}'")
        try:
            if any(ipaddress.ip_network(item, strict=True).prefixlen != 32 for item in client_ips):
                raise ValueError("only proxy host routes are allowed")
        except ValueError as exc:
            raise WireGuardError(f"WireGuard routes must be individual proxy addresses for '{server_id}'") from exc
        if server_id != self.local_host_id or cfg.get("interface", self.interface) != self.interface:
            raise WireGuardError(f"No local WireGuard gateway adapter for '{server_id}'")
        return {**cfg, "_network": network, "_server_ip": server_ip, "_client_ips": client_ips}

    def _run(self, args: List[str], operation: str) -> subprocess.CompletedProcess:
        if not self.has_wg:
            raise WireGuardError("WireGuard tools are not installed")
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=5, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise WireGuardError(f"WireGuard {operation} failed") from exc

    def _assert_interface(self) -> None:
        self._run(["wg", "show", self.interface], "interface check")

    def _live_peer_dump(self) -> str:
        return self._run(["wg", "show", self.interface, "dump"], "peer verification").stdout

    @staticmethod
    def _routes_in_dump(output: str) -> List[str]:
        routes = []
        for line in output.splitlines()[1:]:
            fields = line.split("\t")
            if len(fields) >= 4 and fields[3] != "(none)":
                routes.extend(route.strip() for route in fields[3].split(",") if route.strip())
        return routes

    @staticmethod
    def _keys_in_dump(output: str) -> set[str]:
        return {
            fields[0]
            for line in output.splitlines()[1:]
            if len(fields := line.split("\t")) >= 4 and fields[0] != "(none)"
        }

    def _live_peer_ips(self, public_key: str, output: Optional[str] = None) -> List[str]:
        output = self._live_peer_dump() if output is None else output
        addresses = []
        for line in output.splitlines()[1:]:
            fields = line.split("\t")
            if len(fields) >= 4 and fields[0] == public_key:
                addresses = fields[3].split(",")
                break
        return addresses

    def allocate_vpn_ip(
        self,
        server_id: str,
        existing_members: Optional[List[Dict[str, Any]]] = None,
        live_addresses: Optional[List[str]] = None,
    ) -> str:
        cfg = self._server(server_id)
        # Giữ nguyên IP 10.88.30.1 cho Rack Server và 10.88.30.2 cho Admin MacBook hiện có
        used = {str(ipaddress.ip_address(cfg["_server_ip"])), "10.88.30.1", "10.88.30.2"}
        for peer in self.peers.values():
            if peer.get("server_id") == server_id and peer.get("client_ip"):
                used.add(peer["client_ip"])
        for member in existing_members or []:
            for peer in (member.get("wireguard") or {}).get("devices", []):
                if peer.get("server_id") == server_id and peer.get("client_ip"):
                    used.add(peer["client_ip"])
            legacy = member.get("wireguard") or {}
            if legacy.get("server_id", member.get("assigned_server")) == server_id and legacy.get("client_ip"):
                used.add(legacy["client_ip"].split("/")[0])
        live_networks = []
        for route in live_addresses or []:
            try:
                network = ipaddress.ip_network(route, strict=False)
            except ValueError:
                raise WireGuardError("A live WireGuard peer has an invalid AllowedIPs route")
            if network.version == cfg["_network"].version and network.overlaps(cfg["_network"]):
                live_networks.append(network)
        # Cấp phát IP động bắt đầu từ host thứ 3 trở đi (.3 -> .254)
        for address in cfg["_network"].hosts():
            if str(address) in used:
                continue
            host_route = ipaddress.ip_network(f"{address}/{cfg['_network'].max_prefixlen}")
            if not any(network.overlaps(host_route) for network in live_networks):
                return str(address)
        raise WireGuardError(f"No WireGuard IPs remain for '{server_id}'")

    def register_peer(
        self,
        username: str,
        device_id: str,
        public_key: str,
        server_id: str,
        existing_members: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if not self.is_valid_key(public_key):
            raise WireGuardError("Client public key is invalid")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", device_id):
            raise WireGuardError("Device ID is invalid")
        cfg = self._server(server_id)
        with self._exclusive():
            if device_id in self.peers and self.peers[device_id].get("status") != "REVOKED":
                raise WireGuardError("WireGuard device ID is already registered")
            for peer_id, peer in self.peers.items():
                if peer_id != device_id and peer.get("status") != "REVOKED":
                    if peer.get("public_key") == public_key:
                        raise WireGuardError("WireGuard public key is already registered")
            self._assert_interface()
            live_dump = self._live_peer_dump()
            if public_key in self._keys_in_dump(live_dump):
                raise WireGuardError("WireGuard public key is already present on the live interface")
            client_ip = self.allocate_vpn_ip(
                server_id, existing_members, self._routes_in_dump(live_dump)
            )
            self._run(
                ["wg", "set", self.interface, "peer", public_key, "allowed-ips", f"{client_ip}/32"],
                "peer registration",
            )
            try:
                verified_ips = self._live_peer_ips(public_key)
            except WireGuardError as exc:
                try:
                    self._run(["wg", "set", self.interface, "peer", public_key, "remove"], "rollback")
                except WireGuardError as rollback_error:
                    raise WireGuardError("WireGuard verification failed; manual reconciliation is required") from rollback_error
                raise exc
            if f"{client_ip}/32" not in verified_ips:
                try:
                    self._run(["wg", "set", self.interface, "peer", public_key, "remove"], "rollback")
                except WireGuardError:
                    raise WireGuardError("WireGuard peer verification failed; manual reconciliation is required")
                raise WireGuardError("WireGuard peer was not present after registration")
            peer = {
                "device_id": device_id,
                "username": username,
                "public_key": public_key,
                "client_ip": client_ip,
                "server_id": server_id,
                "server_public_key": cfg["server_public_key"],
                "server_endpoint": cfg["server_endpoint"],
                "allowed_subnets": ", ".join(cfg["_client_ips"]),
                "status": "PENDING_HANDSHAKE",
                "created_at": int(time.time()),
            }
            self.peers[device_id] = peer
            try:
                self._save_peers()
            except WireGuardError:
                try:
                    self._run(["wg", "set", self.interface, "peer", public_key, "remove"], "rollback")
                except WireGuardError:
                    raise WireGuardError("Peer persistence failed; manual reconciliation is required")
                raise
            return dict(peer)

    def revoke_peer(self, device_id: str) -> bool:
        with self._exclusive():
            peer = self.peers.get(device_id)
            if not peer:
                return False
            self._assert_interface()
            self._run(["wg", "set", self.interface, "peer", peer["public_key"], "remove"], "peer removal")
            if self._live_peer_ips(peer["public_key"]):
                raise WireGuardError("WireGuard peer still exists after removal")
            peer["status"] = "REVOKED"
            self._save_peers()
            return True

    def restore_peer(self, device_id: str) -> bool:
        with self._exclusive():
            peer = self.peers.get(device_id)
            if not peer:
                return False
            cfg = self._server(peer["server_id"])
            self._assert_interface()
            self._run(
                ["wg", "set", self.interface, "peer", peer["public_key"], "allowed-ips", f"{peer['client_ip']}/32"],
                "peer restoration",
            )
            if f"{peer['client_ip']}/32" not in self._live_peer_ips(peer["public_key"]):
                raise WireGuardError("WireGuard peer was not present after restoration")
            peer["status"] = "PENDING_HANDSHAKE"
            peer["server_public_key"] = cfg["server_public_key"]
            self._save_peers()
            return True

    def get_device_status(self, device_id: str) -> Dict[str, Any]:
        with self._exclusive():
            peer = self.peers.get(device_id)
            if not peer:
                return {"status": "NOT_CONFIGURED"}
            if peer.get("status") == "REVOKED":
                return {"status": "REVOKED"}
            self._assert_interface()
            output = self._run(["wg", "show", self.interface, "dump"], "device status check").stdout
            for line in output.splitlines()[1:]:
                fields = line.split("\t")
                if len(fields) >= 8 and fields[0] == peer.get("public_key"):
                    handshake = int(fields[4] or 0)
                    status = "ACTIVE" if handshake and time.time() - handshake < 180 else "PENDING_HANDSHAKE"
                    peer.update({"status": status, "last_handshake": handshake or None, "transfer_rx": int(fields[5] or 0), "transfer_tx": int(fields[6] or 0)})
                    self._save_peers()
                    return {key: peer[key] for key in ("device_id", "status", "last_handshake", "transfer_rx", "transfer_tx")}
            peer["status"] = "PARTIAL_FAILURE"
            self._save_peers()
            return {"status": "PARTIAL_FAILURE"}

    async def get_peer_status(self) -> Dict[str, Any]:
        try:
            self._assert_interface()
            output = self._run(["wg", "show", self.interface, "dump"], "status check").stdout
        except WireGuardError:
            return {"interface": self.interface, "status": "NOT_CONFIGURED", "peers_count": 0, "peers": [], "live": False}
        peers = []
        recent = 0
        for line in output.splitlines()[1:]:
            fields = line.split("\t")
            if len(fields) >= 8:
                handshake = int(fields[4] or 0)
                recent += int(handshake > 0 and time.time() - handshake < 180)
                peers.append({"public_key": fields[0][:12] + "…", "allowed_ips": fields[3], "latest_handshake": handshake})
        return {"interface": self.interface, "status": "ACTIVE", "peers_count": len(peers), "connected_peers": recent, "peers": peers, "live": True}
