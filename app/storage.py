"""Persist and read trades from a Google Sheet."""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
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
]

_worksheet: gspread.Worksheet | None = None
_lock = threading.Lock()


def _get_worksheet() -> gspread.Worksheet:
    """Open (and cache) the worksheet, ensuring the header row exists."""
    global _worksheet
    with _lock:
        if _worksheet is not None:
            return _worksheet

        info = json.loads(config.GOOGLE_CREDENTIALS_JSON)
        creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(config.GOOGLE_SHEET_ID)

        try:
            ws = spreadsheet.worksheet(config.WORKSHEET_NAME)
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(
                title=config.WORKSHEET_NAME, rows=1000, cols=len(HEADERS)
            )

        existing = ws.row_values(1)
        if existing != HEADERS:
            ws.update([HEADERS], "A1")

        _worksheet = ws
        return ws


def _append_sync(trade: dict[str, Any], source: str) -> None:
    ws = _get_worksheet()
    row = [
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
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


async def append_trade(trade: dict[str, Any], source: str) -> None:
    await asyncio.to_thread(_append_sync, trade, source)


async def read_trades() -> list[dict[str, Any]]:
    return await asyncio.to_thread(_read_sync)


async def delete_last_trade() -> bool:
    return await asyncio.to_thread(_delete_last_sync)
