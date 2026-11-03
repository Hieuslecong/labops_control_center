import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any

class LabOpsDataService:
    """
    Core data service for LabOps Control Center.
    Manages in-memory state for Demo Mode and interfaces with PostgreSQL/adapters in Production Mode.
    """
    def __init__(self):
        self._init_data()

    def _init_data(self):
        self.servers = [
            {
                "id": "track",
                "name": "Server 01 — Track",
                "hostname": "track.lab.internal",
                "location": "Phòng Lab Hà Nội (Khu A)",
                "status": "online",
                "ip_address": "10.10.1.10",
                "vpn_ip": "10.200.0.1",
                "uptime": "45 ngày 12 giờ",
                "cpu_model": "AMD EPYC 7763 64-Core Processor (128 vCPUs)",
                "cpu_cores": 64,
                "cpu_usage_percent": 42.5,
                "ram_total_gb": 256.0,
                "ram_used_gb": 174.2,
                "ram_usage_percent": 68.0,
                "disk_total_tb": 8.0,
                "disk_used_tb": 4.32,
                "disk_usage_percent": 54.0,
                "disk_type": "ZFS RAID-Z2 (NVMe Pools)",
                "network_in_mbps": 620,
                "network_out_mbps": 580,
                "gpu_count": 3,
                "active_members": 4,
                "active_jobs": 5,
                "incus_version": "6.0.2 LTS",
                "driver_version": "550.120",
                "cuda_version": "12.4",
                "last_updated": "Vừa xong"
            },
            {
                "id": "workstation",
                "name": "Server 02 — Workstation",
                "hostname": "ws.lab.internal",
                "location": "Phòng Lab TP.HCM (Khu B)",
                "status": "online",
                "ip_address": "10.10.2.20",
                "vpn_ip": "10.200.0.2",
                "uptime": "18 ngày 06 giờ",
                "cpu_model": "Intel Xeon Gold 6338 32-Core Processor (64 vCPUs)",
                "cpu_cores": 32,
                "cpu_usage_percent": 28.3,
                "ram_total_gb": 128.0,
                "ram_used_gb": 57.6,
                "ram_usage_percent": 45.0,
                "disk_total_tb": 4.0,
                "disk_used_tb": 1.52,
                "disk_usage_percent": 38.0,
                "disk_type": "ZFS Mirror (Enterprise SSD)",
                "network_in_mbps": 210,
                "network_out_mbps": 180,
                "gpu_count": 1,
                "active_members": 1,
                "active_jobs": 1,
                "incus_version": "6.0.2 LTS",
                "driver_version": "550.120",
                "cuda_version": "12.4",
                "last_updated": "Vừa xong"
            }
        ]

        self.gpus = [
            {
                "id": "track-gpu-01",
                "display_label": "TRACK-GPU-01",
                "server_id": "track",
                "server_name": "Server Track",
                "model": "NVIDIA A30 24GB HBM2",
                "pci_bus_id": "0000:3b:00.0",
                "uuid": "GPU-9b48a1c2-3e4f-4a89-91a2-7b1c4e8d0001",
                "vram_total_mb": 24576,
                "vram_used_mb": 18432,
                "gpu_util_percent": 88,
                "temp_celsius": 64,
                "power_watts": 142,
                "power_limit_watts": 165,
                "allocation_mode": "SHARED",
                "enforcement_state": "DRIVER_ENFORCED_VERIFIED",
                "active_processes": [
                    {"pid": 104231, "name": "python (train_llama3.py)", "username": "hieulc", "workspace": "ws-hieulc", "vram_mb": 12288, "cpu_percent": 380.0},
                    {"pid": 105892, "name": "python (eval_bench.py)", "username": "namnv", "workspace": "ws-namnv", "vram_mb": 6144, "cpu_percent": 110.0}
                ],
                "active_users": ["hieulc", "namnv"],
                "associated_jobs": ["job-101", "job-102"]
            },
            {
                "id": "track-gpu-02",
                "display_label": "TRACK-GPU-02",
                "server_id": "track",
                "server_name": "Server Track",
                "model": "NVIDIA A30 24GB HBM2",
                "pci_bus_id": "0000:5e:00.0",
                "uuid": "GPU-9b48a1c2-3e4f-4a89-91a2-7b1c4e8d0002",
                "vram_total_mb": 24576,
                "vram_used_mb": 12288,
                "gpu_util_percent": 62,
                "temp_celsius": 58,
                "power_watts": 115,
                "power_limit_watts": 165,
                "allocation_mode": "EXCLUSIVE",
                "enforcement_state": "HARDWARE_ENFORCED",
                "active_processes": [
                    {"pid": 109314, "name": "python (qwen_finetune.py)", "username": "tuanpm", "workspace": "ws-tuanpm", "vram_mb": 12288, "cpu_percent": 240.0}
                ],
                "active_users": ["tuanpm"],
                "associated_jobs": ["job-103"]
            },
            {
                "id": "track-gpu-03",
                "display_label": "TRACK-GPU-03",
                "server_id": "track",
                "server_name": "Server Track",
                "model": "NVIDIA A30 24GB HBM2",
                "pci_bus_id": "0000:86:00.0",
                "uuid": "GPU-9b48a1c2-3e4f-4a89-91a2-7b1c4e8d0003",
                "vram_total_mb": 24576,
                "vram_used_mb": 0,
                "gpu_util_percent": 0,
                "temp_celsius": 38,
                "power_watts": 32,
                "power_limit_watts": 165,
                "allocation_mode": "EXCLUSIVE",
                "enforcement_state": "DRIVER_ENFORCED_VERIFIED",
                "active_processes": [],
                "active_users": [],
                "associated_jobs": []
            },
            {
                "id": "ws-gpu-01",
                "display_label": "WS-GPU-01",
                "server_id": "workstation",
                "server_name": "Server Workstation",
                "model": "NVIDIA A30 24GB HBM2",
                "pci_bus_id": "0000:18:00.0",
                "uuid": "GPU-8c37b2d1-2f3e-4b78-8091-6a0b3d7c0004",
                "vram_total_mb": 24576,
                "vram_used_mb": 16384,
                "gpu_util_percent": 75,
                "temp_celsius": 61,
                "power_watts": 128,
                "power_limit_watts": 165,
                "allocation_mode": "SHARED",
                "enforcement_state": "MONITOR_ONLY",
                "active_processes": [
                    {"pid": 20412, "name": "python (diffusion_train.py)", "username": "anhlt", "workspace": "ws-anhlt", "vram_mb": 16384, "cpu_percent": 310.0}
                ],
                "active_users": ["anhlt"],
                "associated_jobs": ["job-104"]
            }
        ]

        self.members = [
            {
                "id": "mem-1",
                "full_name": "Lê Cảnh Hiếu",
                "username": "hieulc",
                "email": "hieulc@lab.ai.vn",
                "role": "LEAD",
                "assigned_server": "track",
                "workspace_name": "ws-hieulc",
                "status": "ACTIVE",
                "ssh_status": "ONLINE",
                "ssh_sessions_count": 2,
                "ssh_public_key_preview": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH... (hieu@macbook)",
                "cpu_limit_cores": 16,
                "ram_limit_gb": 64,
                "storage_quota_gb": 500,
                "gpu_permissions": ["track-gpu-01", "track-gpu-02", "track-gpu-03"],
                "vram_policy": "Full 24GB (Shared)",
                "max_runtime_hours": 72,
                "active_jobs_count": 1,
                "total_gpu_hours": 342.5,
                "created_at": "2024-01-15"
            },
            {
                "id": "mem-2",
                "full_name": "Nguyễn Văn Nam",
                "username": "namnv",
                "email": "namnv@lab.ai.vn",
                "role": "RESEARCHER",
                "assigned_server": "track",
                "workspace_name": "ws-namnv",
                "status": "ACTIVE",
                "ssh_status": "ONLINE",
                "ssh_sessions_count": 1,
                "ssh_public_key_preview": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE... (nam@thinkpad)",
                "cpu_limit_cores": 8,
                "ram_limit_gb": 32,
                "storage_quota_gb": 200,
                "gpu_permissions": ["track-gpu-01"],
                "vram_policy": "Quota 12GB (Shared)",
                "max_runtime_hours": 24,
                "active_jobs_count": 1,
                "total_gpu_hours": 128.0,
                "created_at": "2024-02-01"
            },
            {
                "id": "mem-3",
                "full_name": "Phạm Minh Tuấn",
                "username": "tuanpm",
                "email": "tuanpm@lab.ai.vn",
                "role": "RESEARCHER",
                "assigned_server": "track",
                "workspace_name": "ws-tuanpm",
                "status": "ACTIVE",
                "ssh_status": "ONLINE",
                "ssh_sessions_count": 1,
                "ssh_public_key_preview": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAB... (tuan@desktop)",
                "cpu_limit_cores": 16,
                "ram_limit_gb": 64,
                "storage_quota_gb": 300,
                "gpu_permissions": ["track-gpu-02"],
                "vram_policy": "Full 24GB (Exclusive)",
                "max_runtime_hours": 48,
                "active_jobs_count": 1,
                "total_gpu_hours": 215.3,
                "created_at": "2024-02-10"
            },
            {
                "id": "mem-4",
                "full_name": "Lê Tuấn Anh",
                "username": "anhlt",
                "email": "anhlt@lab.ai.vn",
                "role": "RESEARCHER",
                "assigned_server": "workstation",
                "workspace_name": "ws-anhlt",
                "status": "ACTIVE",
                "ssh_status": "ONLINE",
                "ssh_sessions_count": 1,
                "ssh_public_key_preview": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIM... (anh@linux)",
                "cpu_limit_cores": 12,
                "ram_limit_gb": 48,
                "storage_quota_gb": 250,
                "gpu_permissions": ["ws-gpu-01"],
                "vram_policy": "Full 24GB (Shared)",
                "max_runtime_hours": 36,
                "active_jobs_count": 1,
                "total_gpu_hours": 184.2,
                "created_at": "2024-03-01"
            }
        ]

        self.jobs = [
            {
                "id": "job-101",
                "username": "hieulc",
                "full_name": "Lê Cảnh Hiếu",
                "server_id": "track",
                "workspace": "ws-hieulc",
                "pid": 104231,
                "script": "python train_llama3_lora.py --batch_size=8 --lr=2e-4",
                "gpu_id": "track-gpu-01",
                "cpu_percent": 380.0,
                "ram_used_mb": 28672,
                "vram_used_mb": 12288,
                "start_time": "Hôm nay 08:30",
                "elapsed_time": "05h 42m",
                "status": "RUNNING",
                "ssh_status": "ATTACHED",
                "container_state": "RUNNING"
            },
            {
                "id": "job-102",
                "username": "namnv",
                "full_name": "Nguyễn Văn Nam",
                "server_id": "track",
                "workspace": "ws-namnv",
                "pid": 105892,
                "script": "python eval_benchmarks.py --model_dir=/data/models",
                "gpu_id": "track-gpu-01",
                "cpu_percent": 110.0,
                "ram_used_mb": 12288,
                "vram_used_mb": 6144,
                "start_time": "Hôm nay 11:15",
                "elapsed_time": "02h 57m",
                "status": "RUNNING",
                "ssh_status": "ATTACHED",
                "container_state": "RUNNING"
            },
            {
                "id": "job-103",
                "username": "tuanpm",
                "full_name": "Phạm Minh Tuấn",
                "server_id": "track",
                "workspace": "ws-tuanpm",
                "pid": 109314,
                "script": "python finetune_qwen.py --deepspeed=ds_config.json",
                "gpu_id": "track-gpu-02",
                "cpu_percent": 240.0,
                "ram_used_mb": 34816,
                "vram_used_mb": 12288,
                "start_time": "Hôm nay 06:00",
                "elapsed_time": "08h 12m",
                "status": "RUNNING",
                "ssh_status": "DETACHED (TMUX)",
                "container_state": "RUNNING"
            },
            {
                "id": "job-104",
                "username": "anhlt",
                "full_name": "Lê Tuấn Anh",
                "server_id": "workstation",
                "workspace": "ws-anhlt",
                "pid": 20412,
                "script": "python diffusion_train.py --steps=50000 --res=512",
                "gpu_id": "ws-gpu-01",
                "cpu_percent": 310.0,
                "ram_used_mb": 24576,
                "vram_used_mb": 16384,
                "start_time": "Hôm qua 22:00",
                "elapsed_time": "16h 12m",
                "status": "RUNNING",
                "ssh_status": "DETACHED (TMUX)",
                "container_state": "RUNNING"
            }
        ]

        self.security_reviews = [
            {
                "id": "sec-1",
                "repo_url": "https://github.com/vllm-project/vllm.git",
                "repo_name": "vllm-project/vllm",
                "commit_sha": "a7b3c9d8e2f1",
                "submitted_by": "hieulc",
                "submitted_at": "Hôm nay 10:15",
                "status": "APPROVED",
                "risk_score": 12,
                "findings": {
                    "high": 0,
                    "medium": 1,
                    "low": 2,
                    "crypto_mining": False,
                    "suspicious_networking": False,
                    "privilege_escalation": False,
                    "verified_clean": True
                },
                "summary": "Kho mã nguồn hợp lệ từ upstream vLLM. Đã quét không phát hiện mã độc.",
                "reviewed_by": "Admin (Tự động)"
            },
            {
                "id": "sec-2",
                "repo_url": "https://github.com/research-internal/custom-flash-attn.git",
                "repo_name": "custom-flash-attn",
                "commit_sha": "4f9d8a1c3b5e",
                "submitted_by": "namnv",
                "submitted_at": "Hôm nay 13:40",
                "status": "PENDING_REVIEW",
                "risk_score": 35,
                "findings": {
                    "high": 0,
                    "medium": 2,
                    "low": 4,
                    "crypto_mining": False,
                    "suspicious_networking": True,
                    "privilege_escalation": False,
                    "verified_clean": False
                },
                "summary": "Phát hiện gọi urllib tới IP lạ trong build script. Cần Admin xem xét trước khi duyệt clone vào workspace.",
                "reviewed_by": None
            }
        ]

        self.events = [
            {
                "id": "ev-1",
                "title": "Seminar: Tối ưu hóa suy luận LLM với vLLM & AWQ",
                "type": "SEMINAR",
                "date": "2024-11-08",
                "start_time": "14:00",
                "end_time": "16:00",
                "location": "Phòng Lab Hà Nội & Google Meet",
                "organizer": "Lê Cảnh Hiếu",
                "related_server": None,
                "status": "SCHEDULED",
                "description": "Báo cáo tiến độ triển khai kỹ thuật lượng tử hóa AWQ trên cụm GPU A30."
            },
            {
                "id": "ev-2",
                "title": "Bảo trì định kỳ ZFS Pool & Cập nhật Kernel",
                "type": "MAINTENANCE",
                "date": "2024-11-10",
                "start_time": "22:00",
                "end_time": "02:00",
                "location": "Server 01 — Track",
                "organizer": "System Admin",
                "related_server": "track",
                "status": "SCHEDULED",
                "description": "Scrub ZFS pool và kiểm tra tính toàn vẹn snapshot tự động."
            }
        ]

        self.audit_logs = [
            {
                "id": "aud-1",
                "actor": "admin@labops.local",
                "action": "GPU_ALLOCATED",
                "target_type": "GPU",
                "target_id": "track-gpu-01",
                "timestamp": "Hôm nay 11:15",
                "result": "SUCCESS",
                "correlation_id": "corr-98f21a4e-3c01",
                "details": "Cấp phát 6GB VRAM cho thành viên namnv (job-102)"
            },
            {
                "id": "aud-2",
                "actor": "admin@labops.local",
                "action": "WORKSPACE_CREATED",
                "target_type": "CONTAINER",
                "target_id": "ws-anhlt",
                "timestamp": "Hôm nay 09:30",
                "result": "SUCCESS",
                "correlation_id": "corr-17b84c3d-9a82",
                "details": "Khởi tạo thành công Incus container ws-anhlt trên Server Workstation"
            }
        ]

    def get_summary(self) -> Dict[str, Any]:
        used_vram_gb = round(sum(gpu["vram_used_mb"] for gpu in self.gpus) / 1024, 1)
        active_gpus = sum(1 for gpu in self.gpus if gpu["vram_used_mb"] > 0)
        online_ssh = sum(1 for m in self.members if m["ssh_status"] == "ONLINE")
        running_jobs = sum(1 for j in self.jobs if j["status"] == "RUNNING")
        
        return {
            "servers": {
                "total": len(self.servers),
                "online": sum(1 for s in self.servers if s["status"] == "online"),
                "offline": sum(1 for s in self.servers if s["status"] != "online")
            },
            "gpus": {
                "totalCount": len(self.gpus),
                "model": "NVIDIA A30 24GB",
                "aggregateVramGb": 96,
                "activeCount": active_gpus,
                "usedVramGb": int(used_vram_gb)
            },
            "members": {
                "onlineSsh": online_ssh,
                "activeSessions": sum(m["ssh_sessions_count"] for m in self.members),
                "totalRegistered": len(self.members)
            },
            "jobs": {
                "running": running_jobs,
                "pending": 0,
                "failed": 0
            },
            "gpuHours": {
                "today": 48.6,
                "last7Days": 384.2,
                "previousPeriodChangePercent": 14.8
            },
            "alerts": {
                "critical": 0,
                "warnings": 1,
                "unresolvedIncidents": 0
            }
        }

    def allocate_gpu(self, gpu_id: str, username: str, mode: str, requested_vram_gb: int, job_name: str) -> Dict[str, Any]:
        target_gpu = next((g for g in self.gpus if g["id"] == gpu_id), None)
        if not target_gpu:
            raise ValueError(f"Không tìm thấy GPU với ID {gpu_id}")

        requested_mb = requested_vram_gb * 1024
        available_mb = target_gpu["vram_total_mb"] - target_gpu["vram_used_mb"]
        if requested_mb > available_mb:
            raise ValueError(f"VRAM không đủ: yêu cầu {requested_vram_gb}GB, khả dụng {round(available_mb / 1024, 1)}GB")

        target_gpu["vram_used_mb"] += requested_mb
        target_gpu["gpu_util_percent"] = min(95, target_gpu["gpu_util_percent"] + 35)
        target_gpu["allocation_mode"] = mode
        if username not in target_gpu["active_users"]:
            target_gpu["active_users"].append(username)

        new_job_id = f"job-{uuid.uuid4().hex[:6]}"
        pid = 120000 + len(self.jobs) * 10
        new_process = {
            "pid": pid,
            "name": f"python ({job_name})",
            "username": username,
            "workspace": f"ws-{username}",
            "vram_mb": requested_mb,
            "cpu_percent": 150.0
        }
        target_gpu["active_processes"].append(new_process)
        target_gpu["associated_jobs"].append(new_job_id)

        new_job = {
            "id": new_job_id,
            "username": username,
            "full_name": username,
            "server_id": target_gpu["server_id"],
            "workspace": f"ws-{username}",
            "pid": pid,
            "script": f"python {job_name}",
            "gpu_id": gpu_id,
            "cpu_percent": 150.0,
            "ram_used_mb": 16384,
            "vram_used_mb": requested_mb,
            "start_time": "Vừa tạo",
            "elapsed_time": "00h 01m",
            "status": "RUNNING",
            "ssh_status": "ATTACHED",
            "container_state": "RUNNING"
        }
        self.jobs.insert(0, new_job)

        # Audit log
        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:6]}",
            "actor": "admin@labops.local",
            "action": "GPU_ALLOCATED",
            "target_type": "GPU",
            "target_id": gpu_id,
            "timestamp": "Vừa xong",
            "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Cấp phát {requested_vram_gb}GB VRAM cho {username} ({job_name})"
        })

        return {"success": True, "job_id": new_job_id, "gpu": target_gpu}

    def stop_job(self, job_id: str) -> Dict[str, Any]:
        job = next((j for j in self.jobs if j["id"] == job_id), None)
        if not job:
            raise ValueError(f"Không tìm thấy job {job_id}")

        job["status"] = "STOPPED"
        job["container_state"] = "STOPPED"
        gpu = next((g for g in self.gpus if g["id"] == job["gpu_id"]), None)
        if gpu:
            gpu["vram_used_mb"] = max(0, gpu["vram_used_mb"] - job["vram_used_mb"])
            gpu["gpu_util_percent"] = max(0, gpu["gpu_util_percent"] - 40)
            gpu["active_processes"] = [p for p in gpu["active_processes"] if p["pid"] != job["pid"]]
            if job["username"] in gpu["active_users"]:
                gpu["active_users"].remove(job["username"])
            if job_id in gpu["associated_jobs"]:
                gpu["associated_jobs"].remove(job_id)

        self.audit_logs.insert(0, {
            "id": f"aud-{uuid.uuid4().hex[:6]}",
            "actor": "admin@labops.local",
            "action": "JOB_TERMINATED",
            "target_type": "JOB",
            "target_id": job_id,
            "timestamp": "Vừa xong",
            "result": "SUCCESS",
            "correlation_id": f"corr-{uuid.uuid4().hex[:12]}",
            "details": f"Dừng tác vụ {job_id} của {job['username']} (PID {job['pid']})"
        })

        return {"success": True, "job_id": job_id}

service = LabOpsDataService()

