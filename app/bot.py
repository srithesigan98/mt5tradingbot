"""Handle incoming Telegram updates: analyze trades and reply."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import analyzer, auth, config, storage, telegram, users
from .stats import summarize

log = logging.getLogger("bot")

# When a trader sends several screenshots as one album, Telegram delivers them
# as separate updates sharing a `media_group_id` (and usually only one carries
# the caption). We buffer them briefly and process the whole album as ONE trade.
_MEDIA_GROUP_WAIT = 3.0  # seconds to wait for all photos in an album
_media_groups: dict[str, dict[str, Any]] = {}
_MAX_IMAGES = 8


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


def _trader_name(message: dict[str, Any]) -> str:
    """A human-readable identity for whoever sent the message."""
    sender = message.get("from") or {}
    username = (sender.get("username") or "").strip()
    if username:
        return username
    first = (sender.get("first_name") or "").strip()
    last = (sender.get("last_name") or "").strip()
    return (f"{first} {last}".strip()) or f"user-{sender.get('id', 'unknown')}"


def _confirmation(trade: dict[str, Any], trader: str) -> str:
    outcome = (trade.get("outcome") or "unknown").lower()
    emoji = {"profit": "✅", "loss": "🔻", "breakeven": "⚖️"}.get(outcome, "📝")
    instrument = trade.get("instrument") or "trade"
    direction = (trade.get("direction") or "").upper()

    lines = [f"{emoji} <b>Logged {instrument} {direction}</b>".rstrip()]
    lines.append(f"Trader: {trader}")
    if trade.get("session"):
        lines.append(f"Session: {trade.get('session')}")
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


async def _handle_command(chat_id: int, text: str, trader: str, tg_username: str, user: dict | None) -> bool:
    cmd = text.split()[0].lower().lstrip("/").split("@")[0]

    if cmd in ("start", "help"):
        if user is None:
            handle = f"@{tg_username}" if tg_username else "(no Telegram username set)"
            await telegram.send_message(
                chat_id,
                "👋 <b>Trading Journal Bot</b>\n\n"
                f"Your Telegram username {handle} isn't registered yet, so I can't log "
                "trades for you. Ask the owner to add you with:\n"
                f"<code>/adduser yourlogin yourpassword {tg_username or 'your_tg_username'} SHEET_ID</code>\n\n"
                "(You also need a Telegram @username set in Settings.)",
            )
            return True
        try:
            await storage.add_subscriber(chat_id, user["username"])
        except Exception:  # noqa: BLE001
            log.exception("subscribe on start failed")
        await telegram.send_message(
            chat_id,
            f"👋 <b>Welcome, {_html_escape(user['username'])}!</b>\n\n"
            "Send me a <b>screenshot</b> of any trade with an optional caption (or "
            "describe it in text) and I'll log it to <b>your</b> private journal.\n\n"
            "<b>Commands</b>\n"
            "/stats — your performance summary\n"
            "/news — upcoming high-impact US events\n"
            "/undo — remove your last logged trade\n"
            "/unsubscribe — stop news alerts\n\n"
            "🔔 You're subscribed to high-impact US news alerts (USD &amp; Gold analysis).\n\n"
            f"📊 Log in to your dashboard:\n{_dashboard_url()}",
        )
        return True

    # Admin-only: register a new user.
    if cmd == "adduser":
        if not (user and user.get("role") == "admin"):
            await telegram.send_message(chat_id, "Only the owner can add users.")
            return True
        parts = text.split()
        if len(parts) < 5:
            await telegram.send_message(
                chat_id,
                "Usage: <code>/adduser &lt;login&gt; &lt;password&gt; &lt;telegram_username&gt; &lt;sheet_id&gt;</code>\n\n"
                "First create a new Google Sheet for them, share it with the service "
                "account email (Editor), and paste its ID as the last argument.",
            )
            return True
        login, password, tg, sheet_id = parts[1], parts[2], parts[3].lstrip("@"), parts[4]
        if await storage.get_user_by_login(login) or await users.by_telegram(tg):
            await telegram.send_message(chat_id, f"A user with that login or Telegram username already exists.")
            return True
        await storage.add_user(login, auth.hash_password(password), tg, sheet_id, role="user")
        await telegram.send_message(
            chat_id,
            f"✅ Added <b>{_html_escape(login)}</b> (Telegram @{_html_escape(tg)}).\n"
            "They can now log trades via this bot and log into the dashboard.\n\n"
            "⚠️ Delete your /adduser message above — it contains their password.",
        )
        return True

    # Everything below requires a registered user.
    if user is None:
        await telegram.send_message(chat_id, "You're not registered yet. Send /start for details.")
        return True

    if cmd == "stats":
        trades = await storage.read_trades(user["sheet_id"])
        await telegram.send_message(chat_id, summarize(trades) + f"\n\n📊 {_dashboard_url()}")
        return True

    if cmd == "undo":
        removed = await storage.delete_last_trade(user["sheet_id"])
        await telegram.send_message(
            chat_id,
            "🗑️ Removed your last logged trade." if removed else "Nothing to undo — your journal is empty.",
        )
        return True

    if cmd in ("subscribe", "sub"):
        await storage.add_subscriber(chat_id, user["username"])
        await telegram.send_message(chat_id, "🔔 Subscribed to high-impact US news alerts.")
        return True

    if cmd in ("unsubscribe", "unsub", "stop"):
        await storage.remove_subscriber(chat_id)
        await telegram.send_message(chat_id, "🔕 Unsubscribed from news alerts. Send /subscribe to turn them back on.")
        return True

    if cmd == "news":
        from . import news
        events = await news.upcoming(limit=10)
        if not events:
            await telegram.send_message(chat_id, "No upcoming high-impact US events found for this week.")
        else:
            lines = ["🇺🇸 <b>Upcoming high-impact US events</b>\n"]
            for e in events:
                fc = f" · F: {e['forecast']}" if e["forecast"] else ""
                pv = f" · P: {e['previous']}" if e["previous"] else ""
                lines.append(f"• <b>{_html_escape(e['title'])}</b>\n  {e['time_local']}{fc}{pv}")
            lines.append("\nYou'll get an alert with USD &amp; Gold analysis before and after each.")
            await telegram.send_message(chat_id, "\n".join(lines))
        return True

    return False


def _largest_photo(message: dict[str, Any]) -> dict[str, Any] | None:
    photos = message.get("photo")
    if not photos:
        return None
    return max(photos, key=lambda p: p.get("file_size", 0))


async def _analyze_and_log(
    chat_id: int,
    caption: str,
    images: list[bytes],
    source: str,
    user: dict,
    file_ids: list[str] | None = None,
) -> None:
    await telegram.send_chat_action(chat_id)
    trade = await analyzer.analyze(caption, images)

    if not trade.get("is_trade_related"):
        await telegram.send_message(
            chat_id,
            "I couldn't find a trade result in that. Send a screenshot of a "
            "closed trade, or describe it — e.g. “XAUUSD buy, +$120, 2R”.",
        )
        return

    # Keep all screenshot IDs (comma-separated) so the dashboard can show each.
    file_id_str = ",".join(fid for fid in (file_ids or []) if fid)
    # Tag with the trader named in the message (one account can log for several
    # traders); fall back to the account's login name when none is stated.
    trader_name = (trade.get("trader") or "").strip() or user["username"]
    # Route to THIS account's own sheet (their private database).
    await storage.append_trade(user["sheet_id"], trade, source, trader=trader_name, file_id=file_id_str)
    try:
        await storage.add_subscriber(chat_id, user["username"])
    except Exception:  # noqa: BLE001
        log.exception("auto-subscribe on trade failed")
    await telegram.send_message(chat_id, _confirmation(trade, trader_name))


async def _flush_media_group(mgid: str) -> None:
    """After a short debounce, process a buffered album as one trade."""
    await asyncio.sleep(_MEDIA_GROUP_WAIT)
    grp = _media_groups.pop(mgid, None)
    if not grp:
        return
    chat_id = grp["chat_id"]
    file_ids = grp["file_ids"][:_MAX_IMAGES]
    try:
        images: list[bytes] = []
        for fid in file_ids:
            try:
                images.append(await telegram.get_file_bytes(fid))
            except Exception:  # noqa: BLE001 — skip an image we can't download
                log.exception("Could not download album image %s", fid)
        await _analyze_and_log(
            chat_id, grp["caption"], images, source="album",
            user=grp["user"], file_ids=file_ids,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to process album %s", mgid)
        await _send_error(chat_id, exc)


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
    trader = _trader_name(message)
    tg_username = (message.get("from") or {}).get("username") or ""

    # Resolve the sender to a registered user (None = not registered).
    user = await users.by_telegram(tg_username)

    # Commands
    if text.startswith("/"):
        if await _handle_command(chat_id, text, trader, tg_username, user):
            return

    # Only registered users may log trades — each into their own sheet.
    if user is None:
        await telegram.send_message(
            chat_id,
            "You're not registered to log trades here. Send /start for details, "
            "or ask the owner to add you.",
        )
        return

    photo = _largest_photo(message)
    media_group_id = message.get("media_group_id")

    # Album (multiple screenshots for one trade): buffer and process together.
    if photo and media_group_id:
        grp = _media_groups.get(media_group_id)
        if grp is None:
            grp = {"file_ids": [], "caption": "", "chat_id": chat_id, "user": user}
            _media_groups[media_group_id] = grp
            asyncio.create_task(_flush_media_group(media_group_id))
        grp["file_ids"].append(photo["file_id"])
        if caption and not grp["caption"]:
            grp["caption"] = caption
        return

    try:
        if photo:
            image_bytes = await telegram.get_file_bytes(photo["file_id"])
            await _analyze_and_log(
                chat_id, caption, [image_bytes], source="photo",
                user=user, file_ids=[photo["file_id"]],
            )
        elif text.strip():
            await _analyze_and_log(chat_id, text, [], source="text", user=user)
        else:
            await telegram.send_message(
                chat_id,
                "Send me a screenshot of a trade or describe it in text, and I'll log it. /help for more.",
            )
    except Exception as exc:  # noqa: BLE001 — surface a friendly error, log the detail
        log.exception("Failed to handle update")
        await _send_error(chat_id, exc)


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _send_error(chat_id: int, exc: Exception) -> None:
    # Send a short, safe reason to the chat so problems are diagnosable without
    # digging through server logs. Anthropic/Google error messages describe the
    # problem (auth, permission, etc.) and do not contain our secret keys.
    reason = f"{type(exc).__name__}: {exc}"
    if len(reason) > 300:
        reason = reason[:300] + "…"
    await telegram.send_message(
        chat_id,
        "⚠️ Something went wrong reading that.\n\n"
        f"<b>Reason:</b> <code>{_html_escape(reason)}</code>\n\n"
        "If this keeps happening, share this message and we'll fix it.",
    )
