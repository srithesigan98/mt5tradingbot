"""Thin async wrapper over the Telegram Bot API (webhook mode)."""
from __future__ import annotations

from typing import Any

import httpx

from . import config

_API_BASE = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
_FILE_BASE = f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}"


async def _post(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{_API_BASE}/{method}", json=payload)
        resp.raise_for_status()
        return resp.json()


async def send_message(chat_id: int, text: str) -> None:
    await _post(
        "sendMessage",
        {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
    )


async def send_chat_action(chat_id: int, action: str = "typing") -> None:
    try:
        await _post("sendChatAction", {"chat_id": chat_id, "action": action})
    except httpx.HTTPError:
        pass  # non-critical


async def get_file_bytes(file_id: str) -> bytes:
    """Resolve a Telegram file_id to its path, then download the raw bytes."""
    info = await _post("getFile", {"file_id": file_id})
    file_path = info["result"]["file_path"]
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(f"{_FILE_BASE}/{file_path}")
        resp.raise_for_status()
        return resp.content


async def set_webhook(url: str) -> dict[str, Any]:
    return await _post(
        "setWebhook",
        {
            "url": url,
            "secret_token": config.WEBHOOK_SECRET,
            "allowed_updates": ["message"],
            "drop_pending_updates": False,
        },
    )
