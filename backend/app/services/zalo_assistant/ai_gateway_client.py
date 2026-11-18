"""
9Router AI Gateway Client
Interfaces with local 9Router (http://127.0.0.1:20128/v1) using model 'model_free'.
Automatically detects local SQLite credentials and provides robust error handling.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_9ROUTER_URL = "http://127.0.0.1:20128/v1"
DEFAULT_MODEL = "model_free"

LABOPS_SYSTEM_PROMPT = """Bạn là LabOps AI Assistant — trợ lý vận hành AI Infrastructure chuyên trách của Lab AIoT (SRE Prompt V3).
Mục tiêu của bạn là hỗ trợ các nhà nghiên cứu và quản trị viên quản lý máy chủ, giám sát GPU, tác vụ (Jobs), và cập nhật tình trạng hạ tầng LabOps.

QUY TẮC BẮT BUỘC:
1. Giao tiếp hoàn toàn bằng tiếng Việt chuẩn xác, súc tích, chuyên nghiệp.
2. Thông tin phần cứng hệ thống:
   - Workstation aiot-ws1: ĐANG HOẠT ĐỘNG (PRIMARY / CỤC BỘ), trang bị 1x NVIDIA A30 24GB (WS-GPU-01, PCI 00000000:D8:00.0).
   - Lưu trữ nhận diện đầy đủ: 4 ổ đĩa vật lý (~6.33 TiB): HDD3 3.67 TiB (/mnt/HDD3, mặc định huấn luyện AI), Root 832.8 GiB (/), SDB 915.8 GiB, SDD 915.8 GiB.
   - Server Rack aiotlab-rack: HIỆN NGOẠI TUYẾN (OFFLINE / KHÔNG THỂ TRUY CẬP). Tuyệt đối không gán GPU cục bộ sang Rack server.
3. Quy tắc lọc tác vụ (/jobs):
   - Chỉ hiển thị tác vụ người dùng thực tế (UID >= 1000 như vinh_test, nhannv).
   - Loại bỏ toàn bộ tiến trình hệ thống (root, systemd, daemon, aiot-ws1) và các lệnh shell/watch/tmux.
   - Gom nhóm các worker PyTorch DataLoader theo cú pháp: `{username} • {script} ({count} tiến trình) [{vram_hoặc_cpu}]`.
4. Hỗ trợ các lệnh chính:
   /start - Giới thiệu trợ lý
   /help - Danh sách lệnh hướng dẫn
   /ask <câu hỏi> - Hỏi đáp kỹ thuật, hỗ trợ AI
   /status - Tổng hợp trạng thái toàn bộ máy chủ, CPU, RAM, GPU, 4 ổ đĩa, Services
   /alloc (hoặc /gpus) - Thống kê tài khoản nào đang được cấp phép cho GPU nào
   /jobs - Danh sách tác vụ người dùng đang chạy
   /report - Báo cáo tuần tổng hợp hoạt động Lab
   /quota - Kiểm tra hạn mức tin nhắn Zalo còn lại
5. Trả lời trực tiếp, không sử dụng markdown phức tạp khó đọc trên màn hình điện thoại."""


def _get_active_system_prompt() -> str:
    """Dynamically load system prompt from docs/ZALO_AI_AGENT_PROMPT.md if available."""
    prompt_file = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "docs", "ZALO_AI_AGENT_PROMPT.md")
    )
    if os.path.exists(prompt_file):
        try:
            with open(prompt_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception:
            pass
    return LABOPS_SYSTEM_PROMPT


class AIGatewayClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = 25,
    ):
        self.base_url = (base_url or os.getenv("ROUTER_BASE_URL", DEFAULT_9ROUTER_URL)).rstrip("/")
        self.model = model or os.getenv("ROUTER_MODEL", DEFAULT_MODEL)
        self.timeout = timeout
        self._explicit_key = api_key or os.getenv("ROUTER_API_KEY")

    def _resolve_api_key(self) -> str:
        if self._explicit_key:
            return self._explicit_key

        # Thử đọc trực tiếp từ SQLite của 9Router
        db_path = os.path.expanduser("~/.9router/db/data.sqlite")
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path, timeout=2.0)
                row = conn.execute("SELECT key FROM apiKeys ORDER BY rowid ASC LIMIT 1").fetchone()
                conn.close()
                if row and row[0]:
                    return str(row[0])
            except Exception as exc:
                logger.debug("Không thể đọc 9router sqlite key: %s", exc)

        return ""

    def check_health(self) -> Dict[str, Any]:
        """Kiểm tra kết nối và model với 9Router cục bộ."""
        api_key = self._resolve_api_key()
        try:
            req = urllib.request.Request(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("id") for m in data.get("data", [])]
                model_present = self.model in models
                return {
                    "status": "ONLINE" if resp.status == 200 else "DEGRADED",
                    "base_url": self.base_url,
                    "model": self.model,
                    "model_available": model_present,
                    "available_models_count": len(models),
                }
        except Exception as exc:
            return {
                "status": "OFFLINE",
                "base_url": self.base_url,
                "model": self.model,
                "model_available": False,
                "error": str(exc)[:200],
            }

    def generate_chat_response(
        self,
        messages: List[Dict[str, str]],
        *,
        system_prompt: Optional[str] = None,
        temperature: float = 0.5,
        max_tokens: int = 1000,
    ) -> str:
        api_key = self._resolve_api_key()
        full_messages = []
        active_prompt = system_prompt or _get_active_system_prompt()
        full_messages.append({"role": "system", "content": active_prompt})
        full_messages.extend(messages)

        payload = {
            "model": self.model,
            "messages": full_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        headers = {
            "Content-Type": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data_bytes,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                choices = result.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {}) or {}
                    raw_content = msg.get("content")
                    if raw_content and isinstance(raw_content, str):
                        return raw_content.strip()

                    # Fallback to reasoning_content if content is None
                    reasoning = msg.get("reasoning_content")
                    if reasoning and isinstance(reasoning, str) and reasoning.strip():
                        return reasoning.strip()

                    # Fallback if tool_calls was emitted by agentic model
                    tool_calls = msg.get("tool_calls")
                    if tool_calls and isinstance(tool_calls, list) and len(tool_calls) > 0:
                        return "Hệ thống đã nhận diện yêu cầu tra cứu tài nguyên. Bạn có thể sử dụng trực tiếp lệnh `/status` để xem toàn bộ thông số máy chủ và GPU A30."

                return "Trợ lý không nhận được phản hồi nội dung từ mô hình AI."
        except urllib.error.HTTPError as http_err:
            body = http_err.read().decode("utf-8", errors="ignore")
            logger.error("Lỗi HTTP từ 9Router (%d): %s", http_err.code, body)
            return f"Lỗi cổng kết nối AI Gateway 9Router (HTTP {http_err.code})."
        except Exception as exc:
            logger.error("Lỗi kết nối tới 9Router: %s", exc)
            return f"Không thể kết nối tới mô hình AI trên 9Router: {str(exc)[:150]}"
