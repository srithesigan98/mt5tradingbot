# mt5tradingbot — Telegram-powered Trading Journal

Send a screenshot of any trade (profit, loss, or breakeven) to your Telegram
bot. Claude reads the image, extracts the details, and logs them to a Google
Sheet — and a live website updates automatically so you can check your
performance any time.

```
You send a screenshot ──▶ Telegram bot (webhook)
        │
        ▼
   Claude vision reads it ──▶ { instrument, direction, outcome, P&L, R, notes }
        │
        ▼
   Saved to your Google Sheet
        │
        ▼
   Live dashboard (a real URL you bookmark) — win rate, net P&L, equity curve
```

## What you get
- **Automatic logging** — no forms. A screenshot or a one-line text is enough
  ("XAUUSD buy, +$120, 2R").
- **A TradeZella-style dashboard** at your Render URL: KPI cards with win-rate
  and profit-factor gauges, trade expectancy, avg win/loss, a **monthly P&L
  calendar** (green/red day cells + weekly totals), an equity curve, and a
  trades table. Auto-refreshes every 30s.
- **Multiple traders** — share the bot with someone else and each person's
  trades are tagged with their Telegram username. The dashboard has
  **per-trader tabs** (All / you / them) that recompute every stat, and
  `/stats` breaks results down by trader.
- **Click any trade** → a popup shows the **original screenshot** plus a
  Claude-written explanation of the trade.
- **Your data in a Google Sheet** you fully own and can edit by hand.
- **Bot commands:** `/stats` for a quick summary, `/undo` to remove the last
  entry, `/help`.

### Reading your messages accurately
- Your **written log is the source of truth** — when your caption states entry,
  exit, SL, TP, profit, outcome, date, etc., the bot uses exactly those values
  and only uses the screenshot to fill gaps.
- **Multiple screenshots = one trade.** Send them as a Telegram album and they're
  combined into a single journal entry (all screenshots viewable in the popup).
  Multiple entries/partials are compounded into one trade too.
- Outcome is mapped from your template: **Hit TP → profit, Hit SL → loss,
  Hit BE → breakeven**, with the P&L sign set to match. "SL 50 pips / TP 50 pips"
  is treated as your risk setup, not the trade result.
- Extra template fields (session, setup/strategy, discipline rating) are captured
  and shown on the dashboard and in the trade popup.

### How multi-trader works
Every message carries the sender's Telegram identity. The bot saves that as a
`trader` column in the sheet and tags the trade with it — no configuration
needed. Add a second person by simply sharing your bot's @username with them;
their first logged trade creates their tab automatically. (Tip: if you set
`ALLOWED_TELEGRAM_USER_IDS`, add both people's IDs, comma-separated.)

### How screenshots are stored
Telegram permanently retains every photo sent to a bot. Rather than use paid
persistent disk on Render (its free disk is wiped on each restart), the bot
stores Telegram's `file_id` in the sheet and the dashboard streams the image
back through `/api/image/<file_id>` on demand — free, and it survives restarts.

## Tech
- **FastAPI** web service (one process) running the Telegram **webhook** and
  serving the dashboard — a good fit for Render's free tier.
- **Claude** vision + structured tool use for reliable extraction (`app/analyzer.py`).
- **Google Sheets** via `gspread` for storage (`app/storage.py`).

## Quick start
See **[docs/SETUP.md](docs/SETUP.md)** for the full, click-by-click guide
(Telegram bot, Anthropic key, Google service account, Render deploy).

## Run locally (optional, for testing)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in your secrets
# Expose a public HTTPS URL for Telegram to reach (e.g. ngrok):
#   ngrok http 8000
# then set RENDER_EXTERNAL_URL / PUBLIC_URL in .env to that https URL
uvicorn app.main:app --reload --port 8000
```
Open http://localhost:8000 for the dashboard. The Telegram webhook needs a
public HTTPS URL (a tunnel like ngrok), so the bot side is easiest to test once
deployed on Render.

## Configuration
All settings are environment variables — see `.env.example` for the full list
and `app/config.py` for how they're read. Required: `TELEGRAM_BOT_TOKEN`,
`ANTHROPIC_API_KEY`, `GOOGLE_SHEET_ID`, `GOOGLE_CREDENTIALS_JSON`.

## Project layout
```
app/
  main.py       FastAPI app: webhook + dashboard + /api/trades + /api/image
  bot.py        Telegram update handling (commands, photos, text, trader id)
  analyzer.py   Claude vision → structured trade + written analysis
  storage.py    Google Sheets read/write (incl. trader, file_id, analysis)
  stats.py      Performance metrics, per-trader filtering, calendar buckets
  telegram.py   Telegram Bot API client (webhook mode)
  config.py     Env-var configuration
  static/chart.umd.min.js    Bundled Chart.js (no external CDN needed)
  templates/dashboard.html   The TradeZella-style dashboard
docs/SETUP.md   Step-by-step setup for a non-developer
render.yaml     One-click Render deploy
```

## A note on the "Claude artifact"
A Claude Artifact is a static page and can't run a bot or fetch live data, so
the always-updating journal lives at your Render URL (bookmark it on your
phone). Claude can still generate a one-off artifact snapshot of your stats on
request — but the live surface is the hosted dashboard.
