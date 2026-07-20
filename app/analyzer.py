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


# A strict tool schema forces Claude to return exactly these fields. Nullable
# fields use a ["type", "null"] union so Claude can leave them blank when a
# value isn't visible in the screenshot.
_RECORD_TRADE_TOOL: dict[str, Any] = {
    "name": "record_trade",
    "description": (
        "Record the details of a single trade extracted from a screenshot "
        "and/or a text message. Read numbers carefully from the image."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "is_trade_related": {
                "type": "boolean",
                "description": "True if the message/image is about a specific trade result.",
            },
            "instrument": {
                "type": ["string", "null"],
                "description": "The symbol traded, e.g. XAUUSD, EURUSD, US30, BTCUSD.",
            },
            "direction": {
                "type": "string",
                "enum": ["buy", "sell", "long", "short", "unknown"],
            },
            "outcome": {
                "type": "string",
                "enum": ["profit", "loss", "breakeven", "unknown"],
                "description": "Whether the trade won, lost, or closed at breakeven.",
            },
            "pnl_amount": {
                "type": ["number", "null"],
                "description": "Profit/loss in account currency. Negative for a loss.",
            },
            "pnl_currency": {
                "type": ["string", "null"],
                "description": "Currency of pnl_amount, e.g. USD.",
            },
            "pips": {
                "type": ["number", "null"],
                "description": "Profit/loss in pips or points, if shown. Negative for a loss.",
            },
            "r_multiple": {
                "type": ["number", "null"],
                "description": "Risk multiple (R), e.g. 2.5 for a 2.5R winner.",
            },
            "lot_size": {"type": ["number", "null"]},
            "entry_price": {"type": ["number", "null"]},
            "exit_price": {"type": ["number", "null"]},
            "stop_loss": {"type": ["number", "null"]},
            "take_profit": {"type": ["number", "null"]},
            "trade_date": {
                "type": ["string", "null"],
                "description": "Date of the trade in YYYY-MM-DD if visible, else null.",
            },
            "notes": {
                "type": ["string", "null"],
                "description": "Any extra context, strategy, or the user's own words.",
            },
            "confidence": {
                "type": "number",
                "description": "Your confidence 0-1 that the extracted numbers are correct.",
            },
        },
        "required": [
            "is_trade_related",
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
            "trade_date",
            "notes",
            "confidence",
        ],
    },
}

_SYSTEM_PROMPT = (
    "You are a meticulous trading-journal assistant. You receive a screenshot "
    "of a trade (from MetaTrader 5, a broker app, TradingView, etc.) and/or a "
    "short text message, and you extract the trade result. Read every number "
    "directly from the image — do not guess prices you cannot see. If the "
    "message is clearly not about a specific trade result (a greeting, a "
    "question, general chat), set is_trade_related to false. Always call the "
    "record_trade tool."
)


async def analyze(caption: str, image_bytes: bytes | None, image_media_type: str) -> dict[str, Any]:
    """Analyze a message + optional image and return the record_trade input dict."""
    content: list[dict[str, Any]] = []

    if image_bytes:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image_media_type,
                    "data": base64.standard_b64encode(image_bytes).decode("utf-8"),
                },
            }
        )

    text = caption.strip() if caption else ""
    if not text and image_bytes:
        text = "Extract the trade result from this screenshot."
    elif image_bytes:
        text = f"Message from the trader: {text}\n\nExtract the trade result from the screenshot and message."
    content.append({"type": "text", "text": text or "Extract the trade result from this message."})

    response = await _get_client().messages.create(
        model=config.ANTHROPIC_MODEL,
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
