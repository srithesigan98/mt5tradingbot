"""High-impact economic-news alerts with USD/Gold fundamental analysis.

Pulls a machine-readable economic calendar, finds high-impact events for the
configured countries (USA/USD by default), and broadcasts a Claude-written
fundamental read to subscribed Telegram users — a heads-up before each release
and an interpretation after the actual number prints.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from anthropic import AsyncAnthropic

from . import config, settings, storage, telegram

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

# --- Feed fetching (multi-layer cache) ------------------------------------
# The public calendar feeds rate-limit by IP, and Render's free tier shares
# its outbound IP with many other apps hitting the same feed — so 429s are
# normal. Strategy: short in-memory cache -> network (primary + fallback host,
# with a cooldown after a 429) -> persistent copy in the Google Sheet. The
# weekly schedule barely changes, so serving a stale copy is fine.

_cache: dict[str, Any] = {"at": 0.0, "events": [], "fetched_at": "", "source": ""}
_CACHE_TTL = 300          # seconds an in-memory copy is considered fresh
_ATTEMPT_GAP = 60         # min seconds between network attempts
_COOLDOWN_429 = 900       # back off this long after a 429
_net = {"cooldown_until": 0.0, "last_attempt": 0.0, "last_error": ""}


def _feed_urls() -> list[str]:
    urls = [config.NEWS_FEED_URL]
    # Same data on an alternate host — sometimes rate-limited separately.
    alt = config.NEWS_FEED_URL.replace("https://nfs.", "https://cdn-nfs.")
    if alt != config.NEWS_FEED_URL:
        urls.append(alt)
    return urls


async def _fetch_url(url: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.get(url, headers={"User-Agent": "Mozilla/5.0 (trading-journal)"})
        r.raise_for_status()
        data = r.json()
    return data if isinstance(data, list) else data.get("events", [])


async def _load_sheet_cache() -> bool:
    """Populate the memory cache from the Google Sheet copy. True on success."""
    try:
        fetched_at, js = await asyncio.to_thread(storage.load_news_cache)
        if js:
            events = json.loads(js)
            if events:
                _cache.update(events=events, at=time.time(), fetched_at=fetched_at, source="sheet")
                return True
    except Exception:  # noqa: BLE001
        log.exception("loading news cache from sheet failed")
    return False


async def fetch_events(force: bool = False) -> list[dict[str, Any]]:
    now = time.time()
    if not force and _cache["events"] and (now - _cache["at"] < _CACHE_TTL):
        return _cache["events"]

    # Try the network unless we're throttled or cooling down after a 429.
    if now >= _net["cooldown_until"] and now - _net["last_attempt"] >= _ATTEMPT_GAP:
        _net["last_attempt"] = now
        for url in _feed_urls():
            try:
                events = await _fetch_url(url)
                _cache.update(
                    events=events, at=now, source="network",
                    fetched_at=config.now_local().isoformat(timespec="seconds"),
                )
                _net["last_error"] = ""
                try:  # persist for restarts / rate-limit windows
                    await asyncio.to_thread(storage.save_news_cache, json.dumps(events))
                except Exception:  # noqa: BLE001
                    log.exception("saving news cache to sheet failed")
                return events
            except httpx.HTTPStatusError as exc:
                _net["last_error"] = f"{exc.response.status_code} from feed"
                if exc.response.status_code == 429:
                    _net["cooldown_until"] = now + _COOLDOWN_429
                log.warning("news feed %s -> %s", url, exc.response.status_code)
            except Exception as exc:  # noqa: BLE001
                _net["last_error"] = f"{type(exc).__name__}: {exc}"
                log.warning("news feed %s failed: %s", url, exc)

    # Fall back to whatever we have: memory first, then the sheet copy.
    if _cache["events"]:
        return _cache["events"]
    if await _load_sheet_cache():
        return _cache["events"]
    raise RuntimeError(_net["last_error"] or "news feed unavailable and no cached copy yet")


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
    return _country(ev) in settings.news_countries() and _impact(ev) in settings.news_impact()


def _local_str(dt: datetime | None) -> str:
    return dt.astimezone(config.LOCAL_TZ).strftime("%a %d %b, %H:%M") if dt else "?"


# --- Analysis + broadcast -------------------------------------------------

def _esc(s: Any) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def analyze(ev: dict[str, Any], phase: str) -> dict[str, Any]:
    """Returns {html, body, ...meta}. `html` for Telegram; `body` is plain text
    for the dashboard archive."""
    title = str(ev.get("title") or "US economic event")
    forecast = ev.get("forecast") or "n/a"
    previous = ev.get("previous") or "n/a"
    actual = ev.get("actual") or "n/a"
    dt = _parse_time(ev)
    impact = (_impact(ev) or "high").capitalize()
    if phase == "pre":
        lookahead = settings.news_lookahead_min()
        user = (
            f"Upcoming {impact}-impact US event in ~{lookahead} minutes.\n"
            f"Event: {title}\nForecast: {forecast}\nPrevious: {previous}\n"
            f"Time (local): {_local_str(dt)}\n\n"
            "Give the pre-release read: what to expect, and the likely USD and Gold "
            "reaction if it beats vs misses."
        )
        emoji = "🔔"
        tag = f"{impact}-impact US news in ~{lookahead} min"
    else:
        user = (
            f"{impact}-impact US event just released.\n"
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
    head = f"{emoji} <b>{_esc(title)}</b>\n<i>{tag}</i>\n"
    facts = (
        f"Forecast: {_esc(forecast)} · Previous: {_esc(previous)}"
        if phase == "pre"
        else f"Actual: <b>{_esc(actual)}</b> · Forecast: {_esc(forecast)} · Previous: {_esc(previous)}"
    )
    return {
        "html": f"{head}{facts}\n\n{_esc(body)}",
        "body": body,
        "phase": phase,
        "impact": _impact(ev),
        "event": title,
        "actual": (actual if phase == "post" else ""),
        "forecast": forecast,
        "previous": previous,
    }


_GOLD_SYSTEM = (
    "You are a gold (XAU/USD) market analyst writing a short pre-market outlook "
    "for the trading day. Be practical and specific. Cover the macro backdrop for "
    "gold right now, which of today's scheduled US events matter most for gold and "
    "why, and how the day could unfold. 4-6 sentences. Finish with a line "
    "'Gold bias: <bullish/bearish/neutral> — <short reason>' and then "
    "'Not financial advice.' Do not invent specific price levels or figures you "
    "were not given."
)


async def daily_gold_summary(subs: list[int], events: list[dict[str, Any]]) -> str:
    """Build + broadcast today's gold outlook. Returns the plain-text body."""
    today = config.now_local().date()
    todays = []
    for ev in events:
        if not is_target(ev):
            continue
        dt = _parse_time(ev)
        if not dt or dt.astimezone(config.LOCAL_TZ).date() != today:
            continue
        todays.append(
            f"- {ev.get('title')} ({_impact(ev)}) at {_local_str(dt)}; "
            f"forecast {ev.get('forecast') or 'n/a'}, previous {ev.get('previous') or 'n/a'}"
        )
    events_txt = "\n".join(todays) if todays else "No major US economic events scheduled today."
    user = (
        f"Today is {today}. Scheduled US economic events (local time):\n{events_txt}\n\n"
        "Write today's pre-market gold outlook."
    )
    resp = await _get_client().messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=550,
        system=_GOLD_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    body = "".join(b.text for b in resp.content if b.type == "text").strip()
    html = f"🟡 <b>Daily Gold Outlook — {today}</b>\n\n{_esc(body)}"
    await _broadcast(subs, html)
    await storage.log_analysis({
        "phase": "gold", "impact": "", "event": "Daily gold outlook",
        "actual": "", "forecast": "", "previous": "", "analysis": body,
    })
    return body


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
        if 0 < mins <= settings.news_lookahead_min() and pre_key not in notified:
            result = await analyze(ev, "pre")
            sent += await _broadcast(subs, result["html"])
            await storage.log_analysis({**result, "analysis": result["body"]})
            await asyncio.to_thread(storage.mark_notified, pre_key, title)
            notified.add(pre_key)

        # Interpretation once the actual number has printed.
        post_key = f"{title}|{stamp}|post"
        actual = ev.get("actual")
        if -120 <= mins < 5 and actual not in (None, "") and post_key not in notified:
            result = await analyze(ev, "post")
            sent += await _broadcast(subs, result["html"])
            await storage.log_analysis({**result, "analysis": result["body"]})
            await asyncio.to_thread(storage.mark_notified, post_key, title)
            notified.add(post_key)

    # Daily gold pre-market outlook, once per local day after the configured hour.
    gold_note = ""
    if settings.gold_summary_enabled():
        local_now = config.now_local()
        gold_key = f"gold|{local_now.date().isoformat()}"
        if local_now.hour >= settings.gold_summary_hour() and gold_key not in notified:
            try:
                await daily_gold_summary(subs, events)
                await asyncio.to_thread(storage.mark_notified, gold_key, "Daily gold outlook")
                notified.add(gold_key)
                sent += len(subs)
                gold_note = "gold summary sent"
            except Exception:  # noqa: BLE001 — never let the outlook break event alerts
                log.exception("daily gold summary failed")
                gold_note = "gold summary failed (see logs)"

    out: dict[str, Any] = {"ok": True, "sent": sent, "subscribers": len(subs)}
    if gold_note:
        out["gold"] = gold_note
    return out


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
            "matched": len(out), "error": None,
            "source": _cache.get("source", ""),
            "fetched_at": _cache.get("fetched_at", "")}
