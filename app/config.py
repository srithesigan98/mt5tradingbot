"""Application configuration, loaded from environment variables."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

# Load a local .env file if present (harmless in production, where real env
# vars are set by the host and take precedence over a missing file).
load_dotenv()


def _clean(value: str | None) -> str:
    return (value or "").strip()


TELEGRAM_BOT_TOKEN = _clean(os.getenv("TELEGRAM_BOT_TOKEN"))
WEBHOOK_SECRET = _clean(os.getenv("WEBHOOK_SECRET")) or "changeme"
ANTHROPIC_API_KEY = _clean(os.getenv("ANTHROPIC_API_KEY"))
ANTHROPIC_MODEL = _clean(os.getenv("ANTHROPIC_MODEL")) or "claude-haiku-4-5"
GOOGLE_SHEET_ID = _clean(os.getenv("GOOGLE_SHEET_ID"))
GOOGLE_CREDENTIALS_JSON = _clean(os.getenv("GOOGLE_CREDENTIALS_JSON"))
WORKSHEET_NAME = _clean(os.getenv("WORKSHEET_NAME")) or "Trades"

# Timezone for journal timestamps and "today" defaulting. Malaysia (UTC+8) has
# no daylight saving, so a fixed offset is exact. Override with UTC_OFFSET_HOURS.
try:
    UTC_OFFSET_HOURS = float(_clean(os.getenv("UTC_OFFSET_HOURS")) or "8")
except ValueError:
    UTC_OFFSET_HOURS = 8.0
LOCAL_TZ = timezone(timedelta(hours=UTC_OFFSET_HOURS))


def now_local() -> datetime:
    """Current time in the journal's configured timezone."""
    return datetime.now(LOCAL_TZ)


def today_local_iso() -> str:
    """Today's date (YYYY-MM-DD) in the journal's timezone."""
    return now_local().date().isoformat()

# Render sets RENDER_EXTERNAL_URL automatically. For local testing you can set
# it to your tunnel URL (e.g. an ngrok https URL).
PUBLIC_URL = _clean(os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PUBLIC_URL"))


# --- Economic-news alerts ------------------------------------------------
# Machine-readable calendar feed used for the alert engine (has impact +
# forecast/actual). Forex Factory's free weekly JSON by default.
NEWS_FEED_URL = _clean(os.getenv("NEWS_FEED_URL")) or "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
# Countries to alert on (Forex Factory uses currency codes; USA = "USD").
NEWS_COUNTRIES = {c for c in (_clean(os.getenv("NEWS_COUNTRIES")) or "USD").replace(" ", "").split(",") if c}
# Impact levels to include. Forex Factory rates fewer events "high" than
# Myfxbook does, so we include "medium" by default to match the fuller calendar.
# Set NEWS_IMPACT=high to restrict alerts to tier-1 events (NFP, CPI, FOMC).
NEWS_IMPACT = {s.strip().lower() for s in (_clean(os.getenv("NEWS_IMPACT")) or "high,medium").split(",") if s.strip()}
try:
    NEWS_LOOKAHEAD_MIN = int(_clean(os.getenv("NEWS_LOOKAHEAD_MIN")) or "15")
except ValueError:
    NEWS_LOOKAHEAD_MIN = 15
# Daily gold pre-market summary broadcast once each morning (local hour).
GOLD_SUMMARY_ENABLED = (_clean(os.getenv("GOLD_SUMMARY_ENABLED")) or "true").lower() not in ("0", "false", "no")
try:
    GOLD_SUMMARY_HOUR = int(_clean(os.getenv("GOLD_SUMMARY_HOUR")) or "8")
except ValueError:
    GOLD_SUMMARY_HOUR = 8


# --- Multi-user auth -----------------------------------------------------
# The GOOGLE_SHEET_ID above is the CONTROL sheet: it holds the Users registry
# and the shared news tabs. Each user's trades live in their own sheet.
# Secret used to sign dashboard login cookies (falls back to WEBHOOK_SECRET).
SESSION_SECRET = _clean(os.getenv("SESSION_SECRET")) or (WEBHOOK_SECRET + "-session")
# Bootstrap admin login (always valid, so you can log in before adding users).
OWNER_USERNAME = _clean(os.getenv("OWNER_USERNAME"))
OWNER_PASSWORD = _clean(os.getenv("OWNER_PASSWORD"))
# The admin's Telegram username (no @) — their trades go to the control sheet
# and they may run /adduser.
OWNER_TELEGRAM_USERNAME = _clean(os.getenv("OWNER_TELEGRAM_USERNAME")).lstrip("@").lower()
# How long a dashboard login stays valid.
try:
    SESSION_DAYS = int(_clean(os.getenv("SESSION_DAYS")) or "30")
except ValueError:
    SESSION_DAYS = 30


def _parse_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.replace(" ", "").split(","):
        if part:
            try:
                ids.add(int(part))
            except ValueError:
                pass
    return ids


# If empty, anyone who messages the bot is allowed.
ALLOWED_TELEGRAM_USER_IDS = _parse_ids(_clean(os.getenv("ALLOWED_TELEGRAM_USER_IDS")))


def missing_required() -> list[str]:
    """Return the names of required settings that are not configured."""
    required = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "GOOGLE_SHEET_ID": GOOGLE_SHEET_ID,
        "GOOGLE_CREDENTIALS_JSON": GOOGLE_CREDENTIALS_JSON,
    }
    return [name for name, value in required.items() if not value]
