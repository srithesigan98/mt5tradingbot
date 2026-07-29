"""Persist and read trades from a Google Sheet."""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

from . import config

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Column order for the sheet. `logged_at` is when the bot recorded it;
# `trade_date` is the date of the trade itself (if known).
HEADERS = [
    "logged_at",
    "trade_date",
    "instrument",
    "direction",
    "outcome",
    "pnl_amount",
    "pnl_currency",
    "pips",
    "r_multiple",
    "lot_size",
    "entry_price",
    "exit_price",
    "stop_loss",
    "take_profit",
    "notes",
    "source",
    "confidence",
    # Appended after v1 so existing sheets migrate cleanly (old rows just have
    # blank values in these columns).
    "trader",
    "telegram_file_id",
    "analysis",
    # Appended after v2 — richer template fields.
    "session",
    "setup",
    "discipline",
]

# Secondary worksheets for the news-alert feature (live in the CONTROL sheet).
SUBSCRIBER_HEADERS = ["chat_id", "name", "subscribed_at"]
NEWSLOG_HEADERS = ["key", "event", "notified_at"]
NEWSANALYSIS_HEADERS = [
    "logged_at", "phase", "impact", "event", "actual", "forecast", "previous", "analysis"
]
# Users registry (CONTROL sheet). password_hash is a PBKDF2 hash from auth.py.
USERS_HEADERS = ["username", "password_hash", "telegram", "sheet_id", "role", "created_at"]
# Admin-editable runtime settings (CONTROL sheet "Settings" tab): simple key/value.
SETTINGS_HEADERS = ["key", "value"]

# The control sheet holds the Users registry + shared news tabs; each user's
# trades live in their own sheet (or the control sheet for the owner).
CONTROL = config.GOOGLE_SHEET_ID

_client = None  # cached authorized gspread client
_spreadsheets: dict[str, Any] = {}   # spreadsheet_id -> Spreadsheet
_ws_cache: dict[tuple[str, str], gspread.Worksheet] = {}  # (sheet_id, name) -> ws
_lock = threading.Lock()


def _get_client_obj():
    global _client
    if _client is None:
        info = json.loads(config.GOOGLE_CREDENTIALS_JSON)
        creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
        _client = gspread.authorize(creds)
    return _client


def _open(sheet_id: str):
    sid = sheet_id or CONTROL
    if sid not in _spreadsheets:
        _spreadsheets[sid] = _get_client_obj().open_by_key(sid)
    return _spreadsheets[sid]


def _get_ws(name: str, headers: list[str] | None, sheet_id: str | None = None) -> gspread.Worksheet:
    """Open (and cache) a worksheet by name in the given sheet (default CONTROL),
    ensuring its header row exists. Pass headers=None for a raw sheet."""
    sid = sheet_id or CONTROL
    key = (sid, name)
    with _lock:
        if key in _ws_cache:
            return _ws_cache[key]
        ss = _open(sid)
        try:
            ws = ss.worksheet(name)
        except gspread.WorksheetNotFound:
            ws = ss.add_worksheet(title=name, rows=1000, cols=max(len(headers or []), 4))
        if headers is not None:
            existing = ws.row_values(1)
            if existing != headers:
                ws.update([headers], "A1")
        _ws_cache[key] = ws
        return ws


def _trades_ws(sheet_id: str) -> gspread.Worksheet:
    return _get_ws(config.WORKSHEET_NAME, HEADERS, sheet_id=sheet_id)


def _append_sync(sheet_id: str, trade: dict[str, Any], source: str, trader: str, file_id: str) -> None:
    ws = _trades_ws(sheet_id)
    row = [
        config.now_local().isoformat(timespec="seconds"),
        trade.get("trade_date") or "",
        trade.get("instrument") or "",
        trade.get("direction") or "",
        trade.get("outcome") or "",
        trade.get("pnl_amount") if trade.get("pnl_amount") is not None else "",
        trade.get("pnl_currency") or "",
        trade.get("pips") if trade.get("pips") is not None else "",
        trade.get("r_multiple") if trade.get("r_multiple") is not None else "",
        trade.get("lot_size") if trade.get("lot_size") is not None else "",
        trade.get("entry_price") if trade.get("entry_price") is not None else "",
        trade.get("exit_price") if trade.get("exit_price") is not None else "",
        trade.get("stop_loss") if trade.get("stop_loss") is not None else "",
        trade.get("take_profit") if trade.get("take_profit") is not None else "",
        trade.get("notes") or "",
        source,
        trade.get("confidence") if trade.get("confidence") is not None else "",
        trader,
        file_id,  # may be comma-separated when several screenshots make one trade
        trade.get("analysis") or "",
        trade.get("session") or "",
        trade.get("setup") or "",
        trade.get("discipline_rating") or "",
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")


def _read_sync(sheet_id: str) -> list[dict[str, Any]]:
    ws = _trades_ws(sheet_id)
    return ws.get_all_records()  # list of dicts keyed by header


def _delete_last_sync(sheet_id: str) -> bool:
    ws = _trades_ws(sheet_id)
    values = ws.get_all_values()
    if len(values) <= 1:  # only the header row
        return False
    ws.delete_rows(len(values))
    return True


async def append_trade(sheet_id: str, trade: dict[str, Any], source: str, trader: str = "", file_id: str = "") -> None:
    await asyncio.to_thread(_append_sync, sheet_id, trade, source, trader, file_id)


async def read_trades(sheet_id: str) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_read_sync, sheet_id)


async def delete_last_trade(sheet_id: str) -> bool:
    return await asyncio.to_thread(_delete_last_sync, sheet_id)


# --- Subscribers (for news broadcasts) -----------------------------------

def _add_subscriber_sync(chat_id: int, name: str) -> None:
    ws = _get_ws("Subscribers", SUBSCRIBER_HEADERS)
    existing = {str(r.get("chat_id")) for r in ws.get_all_records()}
    if str(chat_id) in existing:
        return
    ws.append_row([str(chat_id), name, config.now_local().isoformat(timespec="seconds")],
                  value_input_option="USER_ENTERED")


def _remove_subscriber_sync(chat_id: int) -> bool:
    ws = _get_ws("Subscribers", SUBSCRIBER_HEADERS)
    values = ws.get_all_values()
    for i, row in enumerate(values[1:], start=2):  # skip header; 1-based rows
        if row and row[0] == str(chat_id):
            ws.delete_rows(i)
            return True
    return False


def _list_subscriber_ids_sync() -> list[int]:
    ws = _get_ws("Subscribers", SUBSCRIBER_HEADERS)
    ids: list[int] = []
    for r in ws.get_all_records():
        try:
            ids.append(int(r.get("chat_id")))
        except (TypeError, ValueError):
            pass
    return ids


async def add_subscriber(chat_id: int, name: str = "") -> None:
    await asyncio.to_thread(_add_subscriber_sync, chat_id, name)


async def remove_subscriber(chat_id: int) -> bool:
    return await asyncio.to_thread(_remove_subscriber_sync, chat_id)


def list_subscriber_ids() -> list[int]:
    return _list_subscriber_ids_sync()


# --- News-alert dedup log -------------------------------------------------

def load_notified_keys() -> set[str]:
    ws = _get_ws("NewsLog", NEWSLOG_HEADERS)
    return {str(r.get("key")) for r in ws.get_all_records() if r.get("key")}


def mark_notified(key: str, event: str) -> None:
    ws = _get_ws("NewsLog", NEWSLOG_HEADERS)
    ws.append_row([key, event, config.now_local().isoformat(timespec="seconds")],
                  value_input_option="USER_ENTERED")


# --- News analysis archive (current day only; refreshes each day) --------

def _prune_old_analyses_sync(ws: gspread.Worksheet) -> None:
    """Delete rows not from today. Entries are appended chronologically, so old
    rows form a contiguous block at the top."""
    dates = ws.col_values(1)  # logged_at column (index 0 is the header)
    if len(dates) <= 1:
        return
    today = config.today_local_iso()
    first_today = None
    for i, val in enumerate(dates[1:], start=2):  # 1-based sheet rows
        if str(val)[:10] == today:
            first_today = i
            break
    if first_today is None:            # nothing from today -> clear all data rows
        ws.delete_rows(2, len(dates))
    elif first_today > 2:              # drop the older block above today's rows
        ws.delete_rows(2, first_today - 1)


def _log_analysis_sync(entry: dict[str, Any]) -> None:
    ws = _get_ws("NewsAnalysis", NEWSANALYSIS_HEADERS)
    _prune_old_analyses_sync(ws)  # keep only the current day's journal
    ws.append_row(
        [
            config.now_local().isoformat(timespec="seconds"),
            entry.get("phase", ""),
            entry.get("impact", ""),
            entry.get("event", ""),
            entry.get("actual", ""),
            entry.get("forecast", ""),
            entry.get("previous", ""),
            entry.get("analysis", ""),
        ],
        value_input_option="USER_ENTERED",
    )


def _read_analyses_sync(limit: int) -> list[dict[str, Any]]:
    ws = _get_ws("NewsAnalysis", NEWSANALYSIS_HEADERS)
    today = config.today_local_iso()
    rows = [r for r in ws.get_all_records() if str(r.get("logged_at", ""))[:10] == today]
    return list(reversed(rows))[:limit]


async def log_analysis(entry: dict[str, Any]) -> None:
    await asyncio.to_thread(_log_analysis_sync, entry)


async def read_analyses(limit: int = 20) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_read_analyses_sync, limit)


# --- News feed cache (survives restarts & feed rate limits) ---------------
# Layout of the raw "NewsCache" sheet: A1 = fetched_at ISO timestamp,
# A2..An = the feed JSON split into <50k-char chunks (Sheets cell limit).

_CHUNK = 40000


def save_news_cache(events_json: str) -> None:
    ws = _get_ws("NewsCache", None)
    chunks = [events_json[i : i + _CHUNK] for i in range(0, len(events_json), _CHUNK)]
    rows = [[config.now_local().isoformat(timespec="seconds")]] + [[c] for c in chunks]
    ws.clear()
    ws.update(rows, "A1")


def load_news_cache() -> tuple[str, str]:
    """Returns (fetched_at_iso, feed_json) — empty strings if nothing cached."""
    ws = _get_ws("NewsCache", None)
    col = ws.col_values(1)
    if len(col) < 2:
        return "", ""
    return col[0], "".join(col[1:])


# --- Users registry (CONTROL sheet "Users" tab) --------------------------

def _list_users_sync() -> list[dict[str, Any]]:
    ws = _get_ws("Users", USERS_HEADERS)
    return ws.get_all_records()


def _find_user(field: str, value: str) -> dict[str, Any] | None:
    value = (value or "").strip().lower()
    if not value:
        return None
    for r in _list_users_sync():
        if str(r.get(field, "")).strip().lower() == value:
            return r
    return None


def _add_user_sync(username: str, password_hash: str, telegram: str, sheet_id: str, role: str) -> None:
    ws = _get_ws("Users", USERS_HEADERS)
    ws.append_row(
        [username, password_hash, telegram.lstrip("@"), sheet_id, role,
         config.now_local().isoformat(timespec="seconds")],
        value_input_option="USER_ENTERED",
    )


async def list_users() -> list[dict[str, Any]]:
    return await asyncio.to_thread(_list_users_sync)


async def get_user_by_login(username: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_find_user, "username", username)


async def get_user_by_telegram(telegram: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_find_user, "telegram", telegram)


async def add_user(username: str, password_hash: str, telegram: str, sheet_id: str, role: str = "user") -> None:
    await asyncio.to_thread(_add_user_sync, username, password_hash, telegram, sheet_id, role)


def _find_user_row_sync(username: str) -> tuple[int, dict[str, Any]] | None:
    """Return (1-based sheet row, record) for a Users-tab row, or None."""
    uname = (username or "").strip().lower()
    if not uname:
        return None
    ws = _get_ws("Users", USERS_HEADERS)
    for i, r in enumerate(ws.get_all_records(), start=2):  # row 1 is the header
        if str(r.get("username", "")).strip().lower() == uname:
            return i, r
    return None


def _update_user_sync(username: str, fields: dict[str, Any]) -> bool:
    found = _find_user_row_sync(username)
    if not found:
        return False
    idx, record = found
    merged = {**record, **fields}
    ws = _get_ws("Users", USERS_HEADERS)
    ws.update([[merged.get(h, "") for h in USERS_HEADERS]], f"A{idx}")
    return True


def _delete_user_sync(username: str) -> bool:
    found = _find_user_row_sync(username)
    if not found:
        return False
    idx, _record = found
    ws = _get_ws("Users", USERS_HEADERS)
    ws.delete_rows(idx)
    return True


async def update_user(username: str, fields: dict[str, Any]) -> bool:
    return await asyncio.to_thread(_update_user_sync, username, fields)


async def delete_user(username: str) -> bool:
    return await asyncio.to_thread(_delete_user_sync, username)


# --- Admin-editable runtime settings (CONTROL sheet "Settings" tab) -------

def read_settings_sync() -> dict[str, str]:
    ws = _get_ws("Settings", SETTINGS_HEADERS)
    out: dict[str, str] = {}
    for r in ws.get_all_records():
        key = str(r.get("key", "")).strip()
        if key:
            out[key] = str(r.get("value", ""))
    return out


def _save_settings_sync(values: dict[str, str]) -> None:
    ws = _get_ws("Settings", SETTINGS_HEADERS)
    existing = read_settings_sync()
    merged = {**existing, **values}
    rows = [[k, v] for k, v in merged.items()]
    ws.clear()
    ws.update([SETTINGS_HEADERS] + rows, "A1")


async def save_settings(values: dict[str, str]) -> None:
    await asyncio.to_thread(_save_settings_sync, values)
