"""Handle incoming Telegram updates: analyze trades and reply."""
from __future__ import annotations

import logging
from typing import Any

from . import analyzer, config, storage, telegram
from .stats import summarize

log = logging.getLogger("bot")


def _dashboard_url() -> str:
    return config.PUBLIC_URL or "(dashboard URL will appear once deployed)"


def _is_allowed(user_id: int | None) -> bool:
    if not config.ALLOWED_TELEGRAM_USER_IDS:
        return True
    return user_id in config.ALLOWED_TELEGRAM_USER_IDS


def _fmt_num(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        f = float(value)
        return str(int(f)) if f.is_integer() else f"{f:g}"
    except (TypeError, ValueError):
        return str(value)


def _confirmation(trade: dict[str, Any]) -> str:
    outcome = (trade.get("outcome") or "unknown").lower()
    emoji = {"profit": "✅", "loss": "🔻", "breakeven": "⚖️"}.get(outcome, "📝")
    instrument = trade.get("instrument") or "trade"
    direction = (trade.get("direction") or "").upper()

    lines = [f"{emoji} <b>Logged {instrument} {direction}</b>".rstrip()]
    lines.append(f"Result: <b>{outcome.capitalize()}</b>")

    pnl = trade.get("pnl_amount")
    if pnl not in (None, ""):
        cur = trade.get("pnl_currency") or ""
        lines.append(f"P&amp;L: {_fmt_num(pnl)} {cur}".strip())
    if trade.get("pips") not in (None, ""):
        lines.append(f"Pips/points: {_fmt_num(trade.get('pips'))}")
    if trade.get("r_multiple") not in (None, ""):
        lines.append(f"R: {_fmt_num(trade.get('r_multiple'))}")

    conf = trade.get("confidence")
    if isinstance(conf, (int, float)) and conf < 0.6:
        lines.append("\n⚠️ Low confidence reading — double-check the numbers on your journal.")

    lines.append(f"\n📊 Journal: {_dashboard_url()}")
    lines.append("Send /undo if this was wrong.")
    return "\n".join(lines)


async def _handle_command(chat_id: int, text: str) -> bool:
    cmd = text.split()[0].lower().lstrip("/").split("@")[0]

    if cmd in ("start", "help"):
        await telegram.send_message(
            chat_id,
            "👋 <b>Trading Journal Bot</b>\n\n"
            "Send me a <b>screenshot</b> of any trade (profit, loss, or breakeven) "
            "with an optional caption, or just describe the trade in text. I'll read "
            "it and log it to your journal automatically.\n\n"
            "<b>Commands</b>\n"
            "/stats — quick performance summary\n"
            "/undo — remove the last logged trade\n\n"
            f"📊 Your live journal:\n{_dashboard_url()}",
        )
        return True

    if cmd == "stats":
        trades = await storage.read_trades()
        await telegram.send_message(chat_id, summarize(trades) + f"\n\n📊 {_dashboard_url()}")
        return True

    if cmd == "undo":
        removed = await storage.delete_last_trade()
        await telegram.send_message(
            chat_id,
            "🗑️ Removed the last logged trade." if removed else "Nothing to undo — your journal is empty.",
        )
        return True

    return False


def _largest_photo(message: dict[str, Any]) -> dict[str, Any] | None:
    photos = message.get("photo")
    if not photos:
        return None
    return max(photos, key=lambda p: p.get("file_size", 0))


async def _analyze_and_log(chat_id: int, caption: str, image_bytes: bytes | None, source: str) -> None:
    await telegram.send_chat_action(chat_id)
    trade = await analyzer.analyze(caption, image_bytes, "image/jpeg")

    if not trade.get("is_trade_related"):
        await telegram.send_message(
            chat_id,
            "I couldn't find a trade result in that. Send a screenshot of a "
            "closed trade, or describe it — e.g. “XAUUSD buy, +$120, 2R”.",
        )
        return

    await storage.append_trade(trade, source)
    await telegram.send_message(chat_id, _confirmation(trade))


async def handle_update(update: dict[str, Any]) -> None:
    """Entry point for a single Telegram update (already webhook-verified)."""
    message = update.get("message")
    if not message:
        return

    chat_id = message["chat"]["id"]
    user_id = (message.get("from") or {}).get("id")

    if not _is_allowed(user_id):
        await telegram.send_message(chat_id, "Sorry, this journal bot is private.")
        return

    text = message.get("text", "") or ""
    caption = message.get("caption", "") or ""

    # Commands
    if text.startswith("/"):
        if await _handle_command(chat_id, text):
            return

    photo = _largest_photo(message)

    try:
        if photo:
            image_bytes = await telegram.get_file_bytes(photo["file_id"])
            await _analyze_and_log(chat_id, caption, image_bytes, source="photo")
        elif text.strip():
            await _analyze_and_log(chat_id, text, None, source="text")
        else:
            await telegram.send_message(
                chat_id,
                "Send me a screenshot of a trade or describe it in text, and I'll log it. /help for more.",
            )
    except Exception:  # noqa: BLE001 — surface a friendly error, log the detail
        log.exception("Failed to handle update")
        await telegram.send_message(
            chat_id,
            "⚠️ Something went wrong reading that. Please try again in a moment.",
        )
