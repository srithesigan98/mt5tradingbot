"""Compute performance statistics from the raw sheet rows."""
from __future__ import annotations

from typing import Any


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a summary dict plus a normalized, cumulative-equity trade list."""
    total = len(trades)
    wins = losses = breakeven = 0
    gross_profit = 0.0
    gross_loss = 0.0
    r_values: list[float] = []
    equity = 0.0
    points: list[dict[str, Any]] = []

    for i, row in enumerate(trades, start=1):
        outcome = (row.get("outcome") or "").lower()
        pnl = _to_float(row.get("pnl_amount"))
        r = _to_float(row.get("r_multiple"))

        if outcome == "profit":
            wins += 1
        elif outcome == "loss":
            losses += 1
        elif outcome == "breakeven":
            breakeven += 1

        if pnl is not None:
            equity += pnl
            if pnl > 0:
                gross_profit += pnl
            elif pnl < 0:
                gross_loss += abs(pnl)
        if r is not None:
            r_values.append(r)

        points.append(
            {
                "index": i,
                "logged_at": row.get("logged_at", ""),
                "trade_date": row.get("trade_date", ""),
                "instrument": row.get("instrument", ""),
                "direction": row.get("direction", ""),
                "outcome": outcome,
                "pnl_amount": pnl,
                "pnl_currency": row.get("pnl_currency", ""),
                "pips": _to_float(row.get("pips")),
                "r_multiple": r,
                "notes": row.get("notes", ""),
                "cumulative_pnl": round(equity, 2),
            }
        )

    decisive = wins + losses
    win_rate = round(100 * wins / decisive, 1) if decisive else 0.0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else None
    avg_r = round(sum(r_values) / len(r_values), 2) if r_values else None

    pnls = [p["pnl_amount"] for p in points if p["pnl_amount"] is not None]
    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": win_rate,
        "net_pnl": round(equity, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": profit_factor,
        "avg_r": avg_r,
        "best_trade": round(max(pnls), 2) if pnls else None,
        "worst_trade": round(min(pnls), 2) if pnls else None,
        "trades": points,
    }


def summarize(trades: list[dict[str, Any]]) -> str:
    """A short text summary for the /stats Telegram command."""
    s = compute(trades)
    if not s["total_trades"]:
        return "📊 No trades logged yet. Send me a screenshot to get started!"

    lines = [
        "📊 <b>Your journal so far</b>",
        f"Trades: {s['total_trades']} ({s['wins']}W / {s['losses']}L / {s['breakeven']}BE)",
        f"Win rate: {s['win_rate']}%",
        f"Net P&amp;L: {s['net_pnl']:g}",
    ]
    if s["profit_factor"] is not None:
        lines.append(f"Profit factor: {s['profit_factor']}")
    if s["avg_r"] is not None:
        lines.append(f"Avg R: {s['avg_r']}")
    return "\n".join(lines)
