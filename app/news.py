"""High-impact economic-news alerts with USD/Gold fundamental analysis.

Pulls a machine-readable economic calendar, finds high-impact events for the
configured countries (USA/USD by default), and broadcasts a Claude-written
fundamental read to subscribed Telegram users — a heads-up before each release
and an interpretation after the actual number prints.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from anthropic import AsyncAnthropic

from . import config, storage, telegram

log = logging.getLogger("news")

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


_SYSTEM = (
    "You are a concise forex and gold market analyst. Given a high-impact US "
    "economic event, explain in plain language what it means and how it is "
    "likely to move the US Dollar (USD) and Gold (XAU/USD). Keep it to 3-5 short "
    "sentences. Remember the usual mechanism: stronger US data / hawkish surprises "
    "tend to lift USD and weigh on gold, and vice versa — but always reason about "
    "this specific event. Finish with two lines exactly like:\n"
    "USD bias: <bullish/bearish/neutral> — <5-8 word reason>\n"
    "Gold bias: <bullish/bearish/neutral> — <5-8 word reason>\n"
    "Then a final line: 'Not financial advice.' Do not invent an actual figure "
    "that was not provided."
)

# --- Feed fetching (cached) ----------------------------------------------

_cache: dict[str, Any] = {"at": 0.0, "events": []}
_CACHE_TTL = 300  # seconds


async def fetch_events(force: bool = False) -> list[dict[str, Any]]:
    if not force and _cache["events"] and (time.time() - _cache["at"] < _CACHE_TTL):
        return _cache["events"]
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.get(config.NEWS_FEED_URL, headers={"User-Agent": "Mozilla/5.0 (trading-journal)"})
        r.raise_for_status()
        data = r.json()
    events = data if isinstance(data, list) else data.get("events", [])
    _cache["events"] = events
    _cache["at"] = time.time()
    return events


def _parse_time(ev: dict[str, Any]) -> datetime | None:
    s = str(ev.get("date") or ev.get("timestamp") or "").strip()
    if not s:
        return None
    # Unix timestamp?
    if s.isdigit():
        return datetime.fromtimestamp(int(s), tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _impact(ev: dict[str, Any]) -> str:
    return str(ev.get("impact") or ev.get("impactTitle") or "").strip().lower()


def _country(ev: dict[str, Any]) -> str:
    return str(ev.get("country") or ev.get("currency") or "").strip().upper()


def is_target(ev: dict[str, Any]) -> bool:
    return _country(ev) in config.NEWS_COUNTRIES and _impact(ev) in config.NEWS_IMPACT


def _local_str(dt: datetime | None) -> str:
    return dt.astimezone(config.LOCAL_TZ).strftime("%a %d %b, %H:%M") if dt else "?"


# --- Analysis + broadcast -------------------------------------------------

async def analyze(ev: dict[str, Any], phase: str) -> str:
    title = str(ev.get("title") or "US economic event")
    forecast = ev.get("forecast") or "n/a"
    previous = ev.get("previous") or "n/a"
    actual = ev.get("actual") or "n/a"
    dt = _parse_time(ev)
    impact = (_impact(ev) or "high").capitalize()
    if phase == "pre":
        user = (
            f"Upcoming {impact}-impact US event in ~{config.NEWS_LOOKAHEAD_MIN} minutes.\n"
            f"Event: {title}\nForecast: {forecast}\nPrevious: {previous}\n"
            f"Time (local): {_local_str(dt)}\n\n"
            "Give the pre-release read: what to expect, and the likely USD and Gold "
            "reaction if it beats vs misses."
        )
        emoji = "🔔"
        tag = f"{impact}-impact US news in ~{config.NEWS_LOOKAHEAD_MIN} min"
    else:
        user = (
            f"HIGH-impact US event just released.\n"
            f"Event: {title}\nActual: {actual}\nForecast: {forecast}\nPrevious: {previous}\n\n"
            "Interpret the result and give the USD and Gold direction now."
        )
        emoji = "📣"
        tag = "US news released"

    resp = await _get_client().messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=500,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    body = "".join(b.text for b in resp.content if b.type == "text").strip()
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    head = f"{emoji} <b>{esc(title)}</b>\n<i>{tag}</i>\n"
    facts = (
        f"Forecast: {esc(forecast)} · Previous: {esc(previous)}"
        if phase == "pre"
        else f"Actual: <b>{esc(actual)}</b> · Forecast: {esc(forecast)} · Previous: {esc(previous)}"
    )
    return f"{head}{facts}\n\n{esc(body)}"


async def _broadcast(chat_ids: list[int], text: str) -> int:
    sent = 0
    for cid in chat_ids:
        try:
            await telegram.send_message(cid, text)
            sent += 1
        except Exception:  # noqa: BLE001 — a blocked/left user shouldn't stop the rest
            log.warning("Could not send news to %s", cid)
    return sent


async def run_check() -> dict[str, Any]:
    """Called by the /cron/news endpoint. Sends any due pre/post alerts."""
    subs = await asyncio.to_thread(storage.list_subscriber_ids)
    if not subs:
        return {"ok": True, "sent": 0, "note": "no subscribers yet"}

    try:
        events = await fetch_events()
    except Exception as exc:  # noqa: BLE001
        log.exception("news feed fetch failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    now = datetime.now(timezone.utc)
    notified = await asyncio.to_thread(storage.load_notified_keys)
    sent = 0

    for ev in events:
        if not is_target(ev):
            continue
        dt = _parse_time(ev)
        if not dt:
            continue
        mins = (dt - now).total_seconds() / 60.0
        title = str(ev.get("title") or "US event")
        stamp = dt.strftime("%Y%m%d%H%M")

        # Heads-up shortly before the release.
        pre_key = f"{title}|{stamp}|pre"
        if 0 < mins <= config.NEWS_LOOKAHEAD_MIN and pre_key not in notified:
            text = await analyze(ev, "pre")
            sent += await _broadcast(subs, text)
            await asyncio.to_thread(storage.mark_notified, pre_key, title)
            notified.add(pre_key)

        # Interpretation once the actual number has printed.
        post_key = f"{title}|{stamp}|post"
        actual = ev.get("actual")
        if -120 <= mins < 5 and actual not in (None, "") and post_key not in notified:
            text = await analyze(ev, "post")
            sent += await _broadcast(subs, text)
            await asyncio.to_thread(storage.mark_notified, post_key, title)
            notified.add(post_key)

    return {"ok": True, "sent": sent, "subscribers": len(subs)}


async def upcoming(limit: int = 15) -> list[dict[str, Any]]:
    """Upcoming target-country events, for /news and the dashboard."""
    return (await calendar_payload(limit))["events"]


async def calendar_payload(limit: int = 15) -> dict[str, Any]:
    """Upcoming events plus diagnostics (so the dashboard/user can see why a
    list is empty: a feed error vs. genuinely no matching events)."""
    try:
        events = await fetch_events()
    except Exception as exc:  # noqa: BLE001
        log.exception("news feed fetch failed")
        return {"events": [], "total_fetched": 0, "matched": 0,
                "error": f"{type(exc).__name__}: {exc}"}

    now = datetime.now(timezone.utc)
    out = []
    for ev in events:
        if not is_target(ev):
            continue
        dt = _parse_time(ev)
        if not dt or dt < now:
            continue
        out.append({
            "title": str(ev.get("title") or ""),
            "country": _country(ev),
            "impact": _impact(ev),
            "time_utc": dt.isoformat(),
            "time_local": _local_str(dt),
            "forecast": ev.get("forecast") or "",
            "previous": ev.get("previous") or "",
        })
    out.sort(key=lambda e: e["time_utc"])
    return {"events": out[:limit], "total_fetched": len(events),
            "matched": len(out), "error": None}
