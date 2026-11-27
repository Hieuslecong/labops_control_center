#!/usr/bin/env python3
"""
Send a Zalo DM 1-1 announcement introducing the 42 Web Admin Parity features to verified Admin recipients.

Usage:
  PYTHONPATH=backend ./backend/.venv/bin/python backend/scripts/send_admin_parity_announcement.py
  PYTHONPATH=backend ./backend/.venv/bin/python backend/scripts/send_admin_parity_announcement.py --force
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import app.core.config  # noqa: F401,E402  # Loads .env.local before worker service init
from app.services.zalo_assistant.worker import ZaloPollingWorker  # noqa: E402


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Send 42-action Web Admin Parity announcement to Admin Zalo.")
    parser.add_argument("--force", action="store_true", help="Send even if the idempotent cursor already marks it sent.")
    parser.add_argument("--chat-id", action="append", default=[], help="Optional explicit Admin Zalo chat ID(s).")
    args = parser.parse_args()

    worker = ZaloPollingWorker()
    if not worker.bot_token:
        print(json.dumps({"sent": False, "error": "MISSING_ZALO_BOT_TOKEN"}, ensure_ascii=False))
        return 1

    from zalo_bot import Bot

    bot = Bot(token=worker.bot_token)
    result = await worker._maybe_send_parity_release_announcement(
        bot,
        force=bool(args.force),
        target_chat_ids=args.chat_id or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("sent") or result.get("reason") == "ALREADY_SENT" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
