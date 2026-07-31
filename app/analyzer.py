"""Turn a trading screenshot and/or caption into a structured trade record
using Claude's vision + structured tool use."""
from __future__ import annotations

import base64
from typing import Any

from anthropic import AsyncAnthropic

from . import config

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


# Nullable fields use a ["type", "null"] union so Claude can leave them blank
# when a value isn't stated.
_RECORD_TRADE_TOOL: dict[str, Any] = {
    "name": "record_trade",
    "description": (
        "Record the details of ONE trade, extracted from the trader's written "
        "log and any screenshots. Multiple screenshots and multiple entries all "
        "belong to the same single trade."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "is_trade_related": {
                "type": "boolean",
                "description": "True if this is about a specific trade result.",
            },
            "instrument": {
                "type": ["string", "null"],
                "description": "The symbol traded, e.g. XAUUSD, EURJPY, US30, BTCUSD.",
            },
            "trader": {
                "type": ["string", "null"],
                "description": (
                    "The trader's name if the message names one (e.g. a 'Trader:' "
                    "line, or 'by <name>'). One account may be shared by several "
                    "traders. Return null if no trader name is stated."
                ),
            },
            "direction": {
                "type": "string",
                "enum": ["buy", "sell", "long", "short", "unknown"],
            },
            "outcome": {
                "type": "string",
                "enum": ["profit", "loss", "breakeven", "unknown"],
                "description": (
                    "Map from the trader's Outcome field: 'Hit TP'/target -> profit; "
                    "'Hit SL'/stopped out -> loss; 'Hit BE'/breakeven -> breakeven."
                ),
            },
            "pnl_amount": {
                "type": ["number", "null"],
                "description": (
                    "P&L amount in account currency. The template labels this 'Profit' "
                    "even for losses — set the SIGN to match the outcome: negative for a "
                    "loss, positive for a profit, and the stated value (or 0) for breakeven. "
                    "If the screenshot(s) show several partial-close rows for one position, "
                    "this is the SUM of every unique row's profit — deduplicate any row that "
                    "appears in more than one screenshot (same timestamp) before summing."
                ),
            },
            "pnl_currency": {"type": ["string", "null"], "description": "e.g. USD."},
            "pips": {
                "type": ["number", "null"],
                "description": (
                    "Realized result in pips/points, ONLY if a realized pip result is "
                    "stated. Do NOT use SL/TP distances (e.g. 'SL 50 pips') here — those "
                    "are the risk setup, not the result."
                ),
            },
            "r_multiple": {"type": ["number", "null"], "description": "Risk multiple (R)."},
            "lot_size": {"type": ["number", "null"]},
            "entry_price": {
                "type": ["number", "null"],
                "description": "Entry price. If several entries, use the average entry.",
            },
            "exit_price": {"type": ["number", "null"]},
            "stop_loss": {"type": ["number", "null"], "description": "SL price if a price is given."},
            "take_profit": {"type": ["number", "null"], "description": "TP price if a price is given."},
            "trade_date": {
                "type": ["string", "null"],
                "description": (
                    "Trade date normalized to YYYY-MM-DD. Dates are day-first "
                    "(D/M/YYYY), e.g. 20/7/2026 -> 2026-07-20."
                ),
            },
            "session": {
                "type": ["string", "null"],
                "description": "Trading session / time, e.g. 'London / 1530'.",
            },
            "setup": {
                "type": ["string", "null"],
                "description": "The direction & setup / strategy, e.g. 'BUY / Striker Zones M30 signal'.",
            },
            "discipline_rating": {
                "type": ["string", "null"],
                "description": "The discipline rating exactly as written, e.g. '1/3'.",
            },
            "notes": {
                "type": ["string", "null"],
                "description": "The trader's own Notes text (and any psychology/rules answers).",
            },
            "analysis": {
                "type": "string",
                "description": (
                    "A 2-4 sentence explanation of this trade for the journal: the setup, "
                    "what happened, and one constructive observation on execution or risk. "
                    "Plain language."
                ),
            },
            "confidence": {
                "type": "number",
                "description": "Confidence 0-1 that the extracted details are correct.",
            },
        },
        "required": [
            "is_trade_related", "instrument", "trader", "direction", "outcome",
            "pnl_amount", "pnl_currency", "pips", "r_multiple", "lot_size",
            "entry_price", "exit_price", "stop_loss", "take_profit",
            "trade_date", "session", "setup", "discipline_rating",
            "notes", "analysis", "confidence",
        ],
    },
}

_SYSTEM_PROMPT = (
    "You are a meticulous trading-journal assistant for a trader who logs each "
    "trade with a written template plus one or more screenshots.\n\n"
    "CRITICAL RULES:\n"
    "1. The trader's WRITTEN TEXT is the authoritative source for every detail "
    "(instrument, direction, entry, exit, SL, TP, profit, outcome, date, session, "
    "setup, trader name, notes). When the text states a value, use exactly that "
    "value. Use the screenshots only to fill gaps the text doesn't mention.\n"
    "1b. If the message names a trader (e.g. a 'Trader:' line), extract it — one "
    "account may log trades for several traders, and each is tracked separately.\n"
    "2. You may receive MULTIPLE screenshots — they all describe the SAME single "
    "trade. Never split them into multiple trades.\n"
    "3. A screenshot may show several rows (partial closes / scaled-out exits of "
    "one position, e.g. an MT5 history list). ALL of those rows, across ALL "
    "screenshots in the message, belong to this ONE trade. pnl_amount MUST be "
    "the SUM of every row's individual profit/loss — never just one row's value, "
    "and never an average. entry_price is the lot-size-weighted average entry "
    "across the rows; exit_price is the last (most recent) exit.\n"
    "3b. Screenshots of a scrolling list often overlap at the edges: the last "
    "few rows of one screenshot can be the same rows as the first few of the "
    "next (identical timestamp, prices, and profit). Before summing, identify "
    "rows that appear in more than one screenshot by their timestamp and count "
    "each one only ONCE — do not double-count an overlapping row.\n"
    "4. Outcome: 'Hit TP'/target -> profit; 'Hit SL'/stopped out -> loss; "
    "'Hit BE'/breakeven/moved to BE -> breakeven. Make pnl_amount's sign match.\n"
    "5. 'SL 50 pips' / 'TP 50 pips' are the risk setup (distances), NOT the "
    "realized result — never record them as the trade's pip result.\n"
    "6. Normalize dates to YYYY-MM-DD, interpreting them day-first (20/7/2026 -> "
    "2026-07-20). If the trader does NOT state a date, use today's date (it is "
    "provided in the user message).\n"
    "7. If the content is clearly not a trade (a greeting or question), set "
    "is_trade_related=false.\n"
    "Always call the record_trade tool."
)


async def analyze(
    caption: str,
    images: list[bytes] | None = None,
    image_media_type: str = "image/jpeg",
) -> dict[str, Any]:
    """Analyze a written log + any screenshots (all one trade) -> record_trade dict."""
    images = images or []
    content: list[dict[str, Any]] = []

    for img in images:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image_media_type,
                    "data": base64.standard_b64encode(img).decode("utf-8"),
                },
            }
        )

    text = (caption or "").strip()
    if images and text:
        prompt = (
            f"The trader sent {len(images)} screenshot(s) and this written log "
            f"(the log is authoritative):\n\n{text}\n\nExtract the single trade."
        )
    elif images:
        prompt = f"The trader sent {len(images)} screenshot(s) for one trade. Extract the trade."
    else:
        prompt = f"The trader wrote:\n\n{text}\n\nExtract the single trade."
    prompt += f"\n\n(Today's date in the trader's timezone is {config.today_local_iso()}.)"
    content.append({"type": "text", "text": prompt})

    response = await _get_client().messages.create(
        model=config.ANTHROPIC_MODEL_EXTRACTION,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        tools=[_RECORD_TRADE_TOOL],
        tool_choice={"type": "tool", "name": "record_trade"},
        messages=[{"role": "user", "content": content}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "record_trade":
            return dict(block.input)

    raise RuntimeError("Claude did not return a record_trade tool call")
