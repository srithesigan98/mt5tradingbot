"""Runtime-editable settings, persisted in the CONTROL sheet's "Settings" tab.

These override the env-var defaults in config.py so an admin can change news
filters, the daily gold summary, and dashboard appearance from the admin
backend without a redeploy. Anything not explicitly saved here falls back to
config.py.
"""
from __future__ import annotations

import json
from typing import Any

from . import config, storage

# Keys we persist, with their env-derived defaults (as strings, matching how
# they'd be typed in the sheet).
_DEFAULTS: dict[str, str] = {
    "news_countries": ",".join(sorted(config.NEWS_COUNTRIES)),
    "news_impact": ",".join(sorted(config.NEWS_IMPACT)),
    "news_lookahead_min": str(config.NEWS_LOOKAHEAD_MIN),
    "gold_summary_enabled": "true" if config.GOLD_SUMMARY_ENABLED else "false",
    "gold_summary_hour": str(config.GOLD_SUMMARY_HOUR),
    "site_title": "Trading Journal",
    "site_subtitle": "Auto-updated from Telegram",
    "default_view": "summary",
    "kpi_cards": "net_pnl,win_rate,profit_factor,expectancy,avg_win_loss",
    "trader_display_names": "{}",
}

_cache: dict[str, str] | None = None


def _load() -> dict[str, str]:
    global _cache
    if _cache is None:
        try:
            _cache = {**_DEFAULTS, **storage.read_settings_sync()}
        except Exception:  # noqa: BLE001 — never let a bad sheet break the app
            _cache = dict(_DEFAULTS)
    return _cache


def reload() -> None:
    global _cache
    _cache = None
    _load()


async def warm() -> None:
    """Load the sheet copy on a worker thread (call once at startup so the
    first request doesn't block the event loop on a blocking Sheets call)."""
    import asyncio
    await asyncio.to_thread(_load)


def get(key: str) -> str:
    return _load().get(key, _DEFAULTS.get(key, ""))


async def get_all() -> dict[str, str]:
    return dict(_load())


async def save(values: dict[str, str]) -> dict[str, str]:
    """Persist the given keys (only known ones) and refresh the cache."""
    global _cache
    clean = {k: str(v) for k, v in values.items() if k in _DEFAULTS}
    await storage.save_settings(clean)
    _cache = None
    await warm()
    return await get_all()


# --- Typed accessors used by news.py / bot.py -----------------------------

def news_countries() -> set[str]:
    raw = get("news_countries")
    return {c for c in raw.replace(" ", "").upper().split(",") if c}


def news_impact() -> set[str]:
    raw = get("news_impact")
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def news_lookahead_min() -> int:
    try:
        return int(get("news_lookahead_min"))
    except ValueError:
        return config.NEWS_LOOKAHEAD_MIN


def gold_summary_enabled() -> bool:
    return get("gold_summary_enabled").strip().lower() not in ("0", "false", "no")


def gold_summary_hour() -> int:
    try:
        return int(get("gold_summary_hour"))
    except ValueError:
        return config.GOLD_SUMMARY_HOUR


def trader_display_names() -> dict[str, str]:
    try:
        data = json.loads(get("trader_display_names") or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def appearance() -> dict[str, Any]:
    """Public, non-sensitive settings every logged-in user's dashboard reads."""
    cards = [c.strip() for c in get("kpi_cards").split(",") if c.strip()]
    return {
        "site_title": get("site_title") or _DEFAULTS["site_title"],
        "site_subtitle": get("site_subtitle") or _DEFAULTS["site_subtitle"],
        "default_view": get("default_view") or _DEFAULTS["default_view"],
        "kpi_cards": cards or _DEFAULTS["kpi_cards"].split(","),
        "trader_display_names": trader_display_names(),
    }
