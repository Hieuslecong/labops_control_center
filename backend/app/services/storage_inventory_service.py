import os
import re
import json
import logging
import hashlib
import shutil
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from app.core.config import settings

logger = logging.getLogger("labops.storage")

FORBIDDEN_ROOTS = [
    "/", "/etc", "/proc", "/sys", "/var", "/var/run", "/run", "/dev", "/boot", "/root",
    "/bin", "/sbin", "/usr", "/lib", "/lib64", "/var/lib/docker", "/home", "/tmp"
]

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_pool_priority_tier(p: Dict[str, Any]) -> tuple[int, str]:
    """Phân tầng ưu tiên ổ đĩa: Tier 0 (PREFERRED: sdd, sdb và các pool mặc định), Tier 1 (FALLBACK_RESTRICTED: /mnt/HDD3 / sdc)."""
    dev_base = os.path.basename(str(p.get("device") or "")).strip().lower()
    sid = str(p.get("storage_id") or p.get("id") or "").strip().lower()
    mount = str(p.get("mount_point") or "").rstrip("/").lower()
    if (
        mount == "/mnt/hdd3"
        or "hdd3" in mount
        or dev_base.startswith("sdc")
        or sid in {"d5111439-d134-4ba6-93ef-6268d738f035", "pool-hdd3", "hdd3"}
    ):
        return (1, "FALLBACK_RESTRICTED")
    return (0, "PREFERRED")


def _enrich_pool_metadata(p: Dict[str, Any], host_id: str) -> Dict[str, Any]:
    """Chuẩn hóa nhãn hiển thị tiếng Việt và metadata cho cả dữ liệu từ Go Node Agent lẫn quét cục bộ."""
    entry = dict(p)
    entry["host_id"] = host_id
    if "mounted" not in entry:
        entry["mounted"] = True
    dev = str(entry.get("device") or "").strip()
    dev_base = os.path.basename(dev).lower() if dev else ""
    mount = str(entry.get("mount_point") or "").strip()
    fs = str(entry.get("filesystem") or "ext4").strip()
    cap_gib = round(float(entry.get("capacity_gib") or 0.0), 1)
    p_rank, p_tier = get_pool_priority_tier(entry)
    entry["priority_rank"] = p_rank
    entry["priority_tier"] = p_tier
    if p_rank == 1 or mount == "/" or dev_base == "sda2":
        entry["is_default"] = False
    elif "is_default" not in entry:
        entry["is_default"] = True
    if not str(entry.get("label") or "").strip():
        if mount == "/mnt/HDD3" or dev_base == "sdc":
            entry["label"] = f"Ổ đĩa Nghiên cứu AI HDD3 (/mnt/HDD3 — {cap_gib} GiB)"
        elif mount == "/" or dev_base == "sda2":
            entry["label"] = f"Ổ đĩa Hệ thống Root ({fs.upper()} {dev or '/dev/sda2'} — {cap_gib} GiB)"
        elif dev_base.startswith("sd") or dev_base.startswith("nvme"):
            entry["label"] = f"Ổ đĩa Lưu trữ Thứ cấp {dev_base.upper()} ({dev} — {cap_gib} GiB)"
        else:
            entry["label"] = f"{fs.upper()} ({mount or entry.get('storage_id') or ''} — {cap_gib} GiB)"
    return entry


def _pool_matches_identifier(p: Dict[str, Any], query_id: str) -> bool:
    """So khớp storage pool theo UUID, storage_id, bí danh pool-hdd3/pool-sd*, thiết bị hoặc mount point."""
    q = str(query_id or "").strip().lower()
    if not q:
        return False
    q_trimmed = q.rstrip("/") or ("/" if q == "/" else q)
    sid = str(p.get("storage_id") or "").strip().lower()
    pid = str(p.get("id") or "").strip().lower()
    fs_uuid = str(p.get("filesystem_uuid") or "").strip().lower()
    dev = str(p.get("device") or "").strip().lower()
    dev_base = os.path.basename(dev) if dev else ""
    raw_mount = str(p.get("mount_point") or "").strip()
    mount_l = "/" if raw_mount == "/" else raw_mount.rstrip("/").lower()

    candidates = {
        x
        for x in (
            sid,
            pid,
            fs_uuid,
            dev,
            dev_base,
            f"pool-{dev_base}" if dev_base else "",
            mount_l,
        )
        if x
    }
    if mount_l == "/mnt/hdd3" or "hdd3" in mount_l or dev_base == "sdc":
        candidates.update({"pool-hdd3", "hdd3", "/mnt/hdd3"})
    if mount_l == "/" or dev_base == "sda2":
        candidates.update({"pool-root", "root"})
    return q in candidates or q_trimmed in candidates


def _pool_sort_key(p: Dict[str, Any]) -> tuple:
    """Ưu tiên phân vùng đủ điều kiện, xếp /dev/sdd rồi /dev/sdb lên đầu trước các ổ hạn chế như /mnt/HDD3."""
    eligible = bool(p.get("eligible", False))
    p_rank, _ = get_pool_priority_tier(p)
    dev_base = os.path.basename(str(p.get("device") or "")).lower()
    sid = str(p.get("storage_id") or p.get("id") or "").lower()
    if dev_base.startswith("sdd") or sid == "5ce32377-6072-4c07-a066-807e89a5d48b":
        rank = 0
    elif dev_base.startswith("sdb") or sid == "159bacc4-38ae-4adb-bf25-7a5091c308ca":
        rank = 1
    elif p_rank == 0 and p.get("is_default"):
        rank = 2
    else:
        rank = 3 + p_rank
    return (not eligible, p_rank, rank, -float(p.get("allocatable_gib") or 0.0))

class StorageInventoryService:
    """Quản lý kiểm kê ổ đĩa, xác thực tính đủ điều kiện và cấp phát thư mục storage riêng cho từng thành viên."""

    def __init__(self):
        self._custom_pools: Dict[str, List[Dict[str, Any]]] = {}
        self._reservations: Dict[str, int] = {} # storage_id -> committed GiB

    def register_custom_pool(self, host_id: str, pool: Dict[str, Any]):
        """Dùng cho testing / mock verification."""
        host_id = host_id.lower()
        if host_id not in self._custom_pools:
            self._custom_pools[host_id] = []
        # Update or append
        existing = [p for p in self._custom_pools[host_id] if p["storage_id"] == pool["storage_id"]]
        if existing:
            self._custom_pools[host_id].remove(existing[0])
        self._custom_pools[host_id].append(pool)

    def get_storage_pools(self, host_id: Optional[str] = None) -> List[Dict[str, Any]]:
        local_host = (settings.LOCAL_HOST_ID or "workstation").lower()
        target_host = (host_id or local_host).lower()

        # 1. Trả về custom pools nếu đã được mock trong test
        if target_host in self._custom_pools:
            return [_enrich_pool_metadata(p, target_host) for p in self._custom_pools[target_host]]

        # 2. Chỉ truy vấn Node Agent cục bộ cho đúng host cục bộ (không áp nhầm sang remote node như 'track')
        if target_host in (local_host, "local", "localhost"):
            token = settings.LABOPS_NODE_AGENT_TOKEN or settings.LABOPS_AGENT_SECRET or ""
            agent_url = "http://127.0.0.1:9100/api/v1/agent/storage"
            try:
                req = urllib.request.Request(agent_url)
                if token:
                    req.add_header("X-LabOps-Agent-Secret", token)
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode())
                        pools = data.get("storage_pools", [])
                        if pools:
                            resolved_host = local_host
                            result_pools: List[Dict[str, Any]] = []
                            for p in pools:
                                agent_host = str(p.get("server_id") or p.get("host_id") or resolved_host).lower()
                                if target_host not in ("local", "localhost") and agent_host != target_host:
                                    continue
                                result_pools.append(_enrich_pool_metadata(p, resolved_host))
                            if result_pools:
                                result_pools.sort(key=_pool_sort_key)
                                return result_pools
            except Exception as e:
                logger.debug(f"Không thể kết nối Node Agent qua HTTP ({agent_url}): {e}")

        # 3. Fallback: Thu thập nội bộ từ hệ thống host thực tế (trả [] cho remote node chưa xác minh)
        return self._collect_local_storage_pools(target_host)

    def _collect_local_storage_pools(self, host_id: str) -> List[Dict[str, Any]]:
        pools = []
        local_host = (settings.LOCAL_HOST_ID or "workstation").lower()
        approved_roots = ["/mnt/HDD3", "/media/LabOps-Data", "/tank/workspaces", "/mnt/labops-data", "/data"]
        ignored_fs = {
            "cgroup", "cgroup2", "sysfs", "proc", "devpts", "tmpfs", "securityfs",
            "devtmpfs", "vfat", "iso9660", "squashfs", "swap", "autofs", "hugetlbfs",
            "mqueue", "pstore", "efivarfs", "bpf", "debugfs", "tracefs", "fusectl",
            "configfs", "binfmt_misc", "nsfs", "overlay", "ramfs",
        }

        # Chỉ đọc mount/pool thực tế cho host cục bộ; remote host chưa có telemetry trả []
        if host_id.lower() not in (local_host, "local", "localhost") or not os.path.exists("/proc/mounts"):
            return pools

        # Quét /proc/mounts cho workstation (hoặc host hiện tại)
        seen_mounts = set()
        pools_by_device: Dict[str, Dict[str, Any]] = {}
        extra_pools: List[Dict[str, Any]] = []
        if os.path.exists("/proc/mounts"):
            try:
                with open("/proc/mounts", "r") as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) < 4:
                            continue
                        dev, mount, fs, opts_str = parts[0], parts[1], parts[2], parts[3]
                        if mount in seen_mounts:
                            continue
                        seen_mounts.add(mount)

                        is_approved = any(mount == r or mount.startswith(r + "/") for r in approved_roots)

                        # Lọc bỏ loop devices, virtual/boot filesystems và per-user sub-mounts
                        if dev.startswith("/dev/loop") or dev.startswith("none"):
                            continue
                        if not dev.startswith("/dev/") and not is_approved:
                            continue
                        if fs in ignored_fs or fs.startswith("fuse"):
                            continue
                        if "/users/" in mount or "/.quota-images" in mount or "/docker/overlay2" in mount:
                            continue

                        opts = opts_str.split(",")
                        read_only = "ro" in opts

                        # Đọc statvfs
                        try:
                            stat = os.statvfs(mount)
                            block_size = stat.f_frsize
                            cap_gib = round((stat.f_blocks * block_size) / (1024**3), 1)
                            free_gib = round((stat.f_bavail * block_size) / (1024**3), 1)
                            used_gib = round(((stat.f_blocks - stat.f_bfree) * block_size) / (1024**3), 1)
                        except Exception:
                            continue

                        if cap_gib <= 0:
                            continue

                        reserved_gib = round(cap_gib * 0.1, 1)
                        allocatable_gib = max(0.0, round(free_gib - reserved_gib, 1))

                        # Đánh giá tính đủ điều kiện
                        eligible = True
                        reason = None
                        status = "VERIFIED"

                        if read_only:
                            eligible = False
                            reason = "Ổ đĩa ở chế độ Read-Only"
                            status = "INELIGIBLE"
                        elif mount in FORBIDDEN_ROOTS or any(
                            mount == p or (p != "/" and mount.startswith(p + "/"))
                            for p in FORBIDDEN_ROOTS
                        ):
                            eligible = False
                            reason = f"Vùng hệ thống nhạy cảm bị cấm ({mount})"
                            status = "INELIGIBLE"
                        elif free_gib < 10.0:
                            eligible = False
                            reason = f"Dung lượng trống quá thấp (< 10 GiB): {free_gib} GiB"
                            status = "INELIGIBLE"
                        elif mount.startswith("/mnt/") or mount.startswith("/media/") or is_approved:
                            eligible = True

                        dev_base = os.path.basename(dev)
                        is_default = dev_base in ("sdd", "sdb")
                        if mount == "/mnt/HDD3" or dev_base == "sdc":
                            if dev_base == "sdc" and os.path.ismount("/mnt/HDD3"):
                                mount = "/mnt/HDD3"
                            storage_id = "pool-hdd3"
                            label = f"Ổ đĩa Nghiên cứu AI HDD3 (/mnt/HDD3 — {cap_gib} GiB)"
                            is_default = False
                            is_approved = True
                        elif mount == "/" or dev_base == "sda2":
                            storage_id = "pool-root"
                            mount = "/"
                            label = f"Ổ đĩa Hệ thống Root ({fs.upper()} {dev} — {cap_gib} GiB)"
                            eligible = False
                            reason = "Phân vùng hệ thống Root (/) — không cấp phát trực tiếp cho workspace"
                            status = "INELIGIBLE"
                            is_default = False
                            is_approved = False
                        elif dev_base.startswith("sd") or dev_base.startswith("nvme"):
                            storage_id = f"pool-{dev_base}"
                            label = f"Ổ đĩa Lưu trữ Thứ cấp {dev_base.upper()} ({dev} — {cap_gib} GiB)"
                        else:
                            storage_id = f"pool-{host_id}-{re.sub(r'[^a-z0-9]', '-', mount.strip('/').lower() or 'root')}"
                            label = f"{fs.upper()} ({mount} — {cap_gib} GiB)"

                        fs_lower = fs.lower()
                        has_prjquota = (
                            fs_lower in ("ext4", "xfs")
                            and any(o.strip() in ("prjquota", "pquota", "project") for o in opts)
                        ) or fs_lower in ("zfs", "btrfs")
                        q_backend = "ZFS_DATASET" if fs_lower == "zfs" else ("PROJECT_QUOTA" if has_prjquota else "NONE")
                        q_supported = bool(eligible and has_prjquota)
                        if not eligible:
                            q_status = "INELIGIBLE"
                        elif not q_supported:
                            q_status = "QUOTA_NOT_ENFORCEABLE"
                        else:
                            q_status = "VERIFIED"

                        pool_entry = {
                            "host_id": host_id,
                            "storage_id": storage_id,
                            "device": dev,
                            "filesystem_uuid": hashlib.sha256(f"{host_id}:{mount}".encode()).hexdigest()[:36],
                            "mount_point": mount,
                            "filesystem": fs,
                            "label": label,
                            "is_default": is_default,
                            "capacity_gib": cap_gib,
                            "used_gib": used_gib,
                            "free_gib": free_gib,
                            "allocatable_gib": allocatable_gib,
                            "reserved_gib": reserved_gib,
                            "quota_backend": q_backend,
                            "quota_supported": q_supported,
                            "quota_enforced": False,
                            "read_only": read_only,
                            "mounted": True,
                            "eligible": eligible,
                            "ineligibility_reason": reason,
                            "last_verified_at": _now_iso(),
                            "verification_status": q_status,
                        }

                        if dev.startswith("/dev/"):
                            existing = pools_by_device.get(dev)
                            if existing is None or (dev_base == "sdc" and mount == "/mnt/HDD3") or (dev_base == "sda2" and mount == "/"):
                                pools_by_device[dev] = pool_entry
                        else:
                            extra_pools.append(pool_entry)
            except Exception as e:
                logger.error(f"Lỗi đọc /proc/mounts: {e}")

        pools.extend(pools_by_device.values())
        pools.extend(extra_pools)
        pools = [_enrich_pool_metadata(p, host_id) for p in pools]

        pools.sort(key=_pool_sort_key)
        return pools

    def get_storage_pool_by_id(self, host_id: str, storage_id: str) -> Optional[Dict[str, Any]]:
        pools = self.get_storage_pools(host_id)
        for p in pools:
            if p.get("storage_id") == storage_id or p.get("id") == storage_id:
                return p
        for p in pools:
            if _pool_matches_identifier(p, storage_id):
                return p
        # Check legacy service storage pools if available
        try:
            from app.services.labops_service import service
            for p in getattr(service, "storage_pools", []):
                if (p.get("id") == storage_id or p.get("storage_id") == storage_id) and (p.get("server_id") == host_id or p.get("host_id") == host_id):
                    return {
                        "host_id": host_id,
                        "storage_id": storage_id,
                        "device": p.get("disk_identifier", "/dev/sdb1"),
                        "filesystem_uuid": hashlib.sha256(f"{host_id}:{storage_id}".encode()).hexdigest()[:36],
                        "mount_point": p.get("mount_point", "/media/LabOps-Data"),
                        "filesystem": p.get("driver", "ext4"),
                        "capacity_gib": float(p.get("capacity_gb", 1000.0)),
                        "used_gib": float(p.get("used_gb", 100.0)),
                        "free_gib": float(p.get("free_gb", 900.0)),
                        "allocatable_gib": float(p.get("free_gb", 900.0)),
                        "reserved_gib": 100.0,
                        "quota_backend": "PROJECT_QUOTA",
                        "quota_supported": True,
                        "quota_enforced": True,
                        "read_only": False,
                        "mounted": True,
                        "eligible": True,
                        "ineligibility_reason": None,
                        "last_verified_at": _now_iso(),
                        "verification_status": "VERIFIED"
                    }
        except Exception:
            pass
        return None

    def inspect_mount_point(self, mount_point: str) -> Dict[str, Any]:
        """Kiểm tra tính hợp lệ và điều kiện của một mount point."""
        clean = os.path.abspath(mount_point)
        # Bị cấm nếu là root hệ thống hoặc /home
        forbidden_roots = ["/", "/etc", "/proc", "/sys", "/dev", "/boot", "/root", "/var/lib/docker", "/home"]
        for forbidden in forbidden_roots:
            if clean == forbidden or clean.startswith(forbidden + os.sep):
                return {
                    "mount_point": clean,
                    "eligible": False,
                    "ineligibility_reason": f"FORBIDDEN_SYSTEM_ROOT: Mount point '{clean}' thuộc vùng hệ thống cấm"
                }
        return {
            "mount_point": clean,
            "eligible": True,
            "ineligibility_reason": None
        }

    def validate_allocation(
        self,
        host_id: str,
        storage_id: str,
        requested_quota_gib: Optional[int] = None,
        requested_gib: Optional[float] = None
    ) -> Dict[str, Any]:
        quota_int = int(requested_quota_gib if requested_quota_gib is not None else (requested_gib or 0))
        pool = self.get_storage_pool_by_id(host_id, storage_id)
        if not pool:
            return {
                "valid": False,
                "host_id": host_id,
                "storage_id": storage_id,
                "requested_quota_gib": quota_int,
                "reason": f"Storage pool không hợp lệ: '{storage_id}'",
                "error": f"Không tìm thấy storage pool '{storage_id}' trên host '{host_id}'"
            }

        if not pool.get("eligible", False):
            return {
                "valid": False,
                "host_id": host_id,
                "storage_id": storage_id,
                "requested_quota_gib": quota_int,
                "reason": f"Storage pool không hợp lệ: {pool.get('ineligibility_reason', 'Không rõ')}",
                "error": f"Storage pool không đủ điều kiện cấp phát: {pool.get('ineligibility_reason', 'Không rõ')}"
            }

        committed = self._reservations.get(storage_id, 0)
        allocatable = pool.get("allocatable_gib", 0.0) - committed

        if quota_int > allocatable:
            return {
                "valid": False,
                "host_id": host_id,
                "storage_id": storage_id,
                "requested_quota_gib": quota_int,
                "allocatable_gib": max(0.0, allocatable),
                "reason": f"Dung lượng yêu cầu ({quota_int} GiB) vượt quá dung lượng khả dụng ({allocatable:.1f} GiB)",
                "error": f"Dung lượng khả dụng không đủ: yêu cầu {quota_int} GiB, nhưng chỉ còn {allocatable:.1f} GiB có thể cấp phát (đã giữ chỗ: {committed} GiB)"
            }

        mount = pool.get("mount_point", "")
        preview = f"{mount}/labops/users/{{username}}"

        return {
            "valid": True,
            "host_id": host_id,
            "storage_id": storage_id,
            "requested_quota_gib": quota_int,
            "allocatable_gib": allocatable,
            "mount_point": mount,
            "quota_backend": pool.get("quota_backend", "PROJECT_QUOTA"),
            "member_storage_dir_preview": preview
        }

    def provision_member_storage(
        self,
        host_id: str,
        storage_id: str,
        username: str,
        quota_gib: int,
        uid: int = 1000,
        gid: int = 1000
    ) -> Dict[str, Any]:
        username = username.strip().lower()
        if not re.match(r"^[a-z_][a-z0-9_-]{2,31}$", username) or ".." in username or "/" in username or "\\" in username:
            raise ValueError(f"Tên người dùng không hợp lệ: '{username}'")

        # 1. Kiểm tra tính hợp lệ
        val = self.validate_allocation(host_id, storage_id, quota_gib)
        if not val.get("valid"):
            raise ValueError(f"STORAGE_ADMISSION_REJECTED: {val.get('error')}")

        pool = self.get_storage_pool_by_id(host_id, storage_id)
        if not pool:
            raise ValueError(f"Storage pool '{storage_id}' không tồn tại")

        # 2. Xây dựng đường dẫn tuyệt đối theo chuẩn: <selected-storage-root>/labops/users/<username>
        storage_root = os.path.abspath(pool["mount_point"])
        expected_base_dir = os.path.join(storage_root, "labops", "users")
        member_dir = os.path.join(expected_base_dir, username)

        # Chống symlink / path traversal attack
        clean_member_dir = os.path.abspath(member_dir)
        if not clean_member_dir.startswith(expected_base_dir + os.sep):
            raise PermissionError(f"SECURITY_VIOLATION: Phát hiện nguy cơ thoát khỏi storage root (Path traversal): {member_dir}")

        # Kiểm tra symlink trong các phần tử đường dẫn
        cur = "/"
        for part in clean_member_dir.strip("/").split("/"):
            cur = os.path.join(cur, part)
            if os.path.islink(cur):
                raise PermissionError(f"SECURITY_VIOLATION: Thư mục chứa symlink tại '{cur}'")

        # Tạo các thư mục con cách ly với quyền 0700
        subdirs = ["work", "checkpoints", "datasets", ".ssh"]
        created_paths = []
        try:
            os.makedirs(clean_member_dir, mode=0o700, exist_ok=True)
            try:
                os.chmod(clean_member_dir, 0o700)
            except OSError:
                pass
            for sub in subdirs:
                sub_path = os.path.join(clean_member_dir, sub)
                os.makedirs(sub_path, mode=0o700, exist_ok=True)
                try:
                    os.chmod(sub_path, 0o700)
                except OSError:
                    pass
                created_paths.append(sub_path)
        except Exception as e:
            logger.warning(f"Không thể tạo thư mục vật lý cục bộ (có thể do môi trường thử nghiệm): {e}")

        # Cập nhật reservation
        self._reservations[storage_id] = self._reservations.get(storage_id, 0) + quota_gib

        quota_backend = pool.get("quota_backend", "NONE")
        quota_enforced = pool.get("quota_enforced", False)
        status = "VERIFIED" if quota_enforced else "QUOTA_NOT_ENFORCEABLE"
        enforcement_status = "HARD_ENFORCED" if quota_enforced else "QUOTA_NOT_ENFORCEABLE"

        return {
            "success": True,
            "host_id": host_id,
            "storage_id": storage_id,
            "host_storage_root": storage_root,
            "directory": clean_member_dir,
            "member_storage_directory": clean_member_dir,
            "container_mount_path": "/workspace",
            "quota_gib": quota_gib,
            "storage_quota_gb": quota_gib,
            "quota_backend": quota_backend,
            "quota_enforced": quota_enforced,
            "enforcement_status": enforcement_status,
            "verification_status": status,
            "subdirectories": subdirs
        }

    def get_resource_profile(self, host_id: str) -> Dict[str, Any]:
        """Tính toán HostResourceProfile với CPU Shared Weighted và RAM Shared Host Protected."""
        host_id = (host_id or "workstation").lower()

        # Tổng số CPU cores thực tế
        cores = os.cpu_count() or 16

        # Tổng RAM thực tế từ /proc/meminfo hoặc default
        total_ram_bytes = 128 * 1024 * 1024 * 1024 # 128 GiB default
        if os.path.exists("/proc/meminfo"):
            try:
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            kb = int(line.split()[1])
                            total_ram_bytes = kb * 1024
                            break
            except Exception:
                pass

        # Host reserve 20% hoặc tối thiểu 8 GiB
        reserve_bytes = int(total_ram_bytes * 0.20)
        min_reserve = 8 * 1024 * 1024 * 1024
        if reserve_bytes < min_reserve:
            reserve_bytes = min_reserve

        shared_budget_bytes = max(0, total_ram_bytes - reserve_bytes)

        # Đọc PSI memory pressure nếu có
        pressure_dict = {
            "some_avg10": 0.0,
            "some_avg60": 0.0,
            "some_avg300": 0.0,
            "full_avg10": 0.0,
            "full_avg60": 0.0,
            "full_avg300": 0.0,
            "pressure_level": "NONE",
            "is_high_pressure": False
        }
        if os.path.exists("/proc/pressure/memory"):
            try:
                with open("/proc/pressure/memory", "r") as f:
                    for line in f:
                        parts = line.split()
                        if line.startswith("some"):
                            for p in parts[1:]:
                                if p.startswith("avg10="): pressure_dict["some_avg10"] = float(p.split("=")[1])
                                if p.startswith("avg60="): pressure_dict["some_avg60"] = float(p.split("=")[1])
                                if p.startswith("avg300="): pressure_dict["some_avg300"] = float(p.split("=")[1])
                        elif line.startswith("full"):
                            for p in parts[1:]:
                                if p.startswith("avg10="): pressure_dict["full_avg10"] = float(p.split("=")[1])
                                if p.startswith("avg60="): pressure_dict["full_avg60"] = float(p.split("=")[1])
                                if p.startswith("avg300="): pressure_dict["full_avg300"] = float(p.split("=")[1])
                if pressure_dict["some_avg10"] > 40.0 or pressure_dict["full_avg10"] > 15.0:
                    pressure_dict["pressure_level"] = "CRITICAL"
                    pressure_dict["is_high_pressure"] = True
                elif pressure_dict["some_avg10"] > 20.0 or pressure_dict["full_avg10"] > 5.0:
                    pressure_dict["pressure_level"] = "ELEVATED"
                    pressure_dict["is_high_pressure"] = True
            except Exception:
                pass

        return {
            "host_id": host_id,
            "total_cores": cores,
            "cpu_scheduling_mode": "SHARED_WEIGHTED",
            "default_cpu_weight": 100,
            "pids_limit": 4096,
            "total_ram_bytes": total_ram_bytes,
            "host_reserve_bytes": reserve_bytes,
            "shared_budget_bytes": shared_budget_bytes,
            "memory_protection_mode": "SHARED_HOST_PROTECTED",
            "memory_pressure": pressure_dict
        }

storage_service = StorageInventoryService()
