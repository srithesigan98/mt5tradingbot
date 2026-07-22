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

# Secondary worksheets for the news-alert feature.
SUBSCRIBER_HEADERS = ["chat_id", "name", "subscribed_at"]
NEWSLOG_HEADERS = ["key", "event", "notified_at"]
NEWSANALYSIS_HEADERS = [
    "logged_at", "phase", "impact", "event", "actual", "forecast", "previous", "analysis"
]

_spreadsheet = None  # cached gspread Spreadsheet
_ws_cache: dict[str, gspread.Worksheet] = {}
_lock = threading.Lock()


def _get_spreadsheet():
    global _spreadsheet
    if _spreadsheet is None:
        info = json.loads(config.GOOGLE_CREDENTIALS_JSON)
        creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
        _spreadsheet = gspread.authorize(creds).open_by_key(config.GOOGLE_SHEET_ID)
    return _spreadsheet


def _get_ws(name: str, headers: list[str]) -> gspread.Worksheet:
    """Open (and cache) a worksheet by name, ensuring its header row exists."""
    with _lock:
        if name in _ws_cache:
            return _ws_cache[name]
        ss = _get_spreadsheet()
        try:
            ws = ss.worksheet(name)
        except gspread.WorksheetNotFound:
            ws = ss.add_worksheet(title=name, rows=1000, cols=max(len(headers), 4))
        existing = ws.row_values(1)
        if existing != headers:
            ws.update([headers], "A1")
        _ws_cache[name] = ws
        return ws


def _get_worksheet() -> gspread.Worksheet:
    return _get_ws(config.WORKSHEET_NAME, HEADERS)


def _append_sync(trade: dict[str, Any], source: str, trader: str, file_id: str) -> None:
    ws = _get_worksheet()
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


def _read_sync() -> list[dict[str, Any]]:
    ws = _get_worksheet()
    return ws.get_all_records()  # list of dicts keyed by header


def _delete_last_sync() -> bool:
    ws = _get_worksheet()
    values = ws.get_all_values()
    if len(values) <= 1:  # only the header row
        return False
    ws.delete_rows(len(values))
    return True


async def append_trade(trade: dict[str, Any], source: str, trader: str = "", file_id: str = "") -> None:
    await asyncio.to_thread(_append_sync, trade, source, trader, file_id)


async def read_trades() -> list[dict[str, Any]]:
    return await asyncio.to_thread(_read_sync)


async def delete_last_trade() -> bool:
    return await asyncio.to_thread(_delete_last_sync)


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


# --- News analysis archive (shown on the dashboard) ----------------------

def _log_analysis_sync(entry: dict[str, Any]) -> None:
    ws = _get_ws("NewsAnalysis", NEWSANALYSIS_HEADERS)
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
    return list(reversed(ws.get_all_records()))[:limit]


async def log_analysis(entry: dict[str, Any]) -> None:
    await asyncio.to_thread(_log_analysis_sync, entry)


async def read_analyses(limit: int = 20) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_read_analyses_sync, limit)
