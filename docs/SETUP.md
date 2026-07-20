# Setup Guide

This walks you through everything from zero to a live trading journal. No prior
experience needed — follow the steps in order. Total time: ~20–30 minutes.

You'll collect **6 secrets** along the way. Keep them in a note; you'll paste
them into Render at the end.

---

## Part 1 — Create your Telegram bot (5 min)

1. Open Telegram and search for **@BotFather** (the one with the blue checkmark).
2. Send `/newbot`.
3. Give it a name (e.g. *My Trading Journal*) and a username ending in `bot`
   (e.g. `my_trading_journal_bot`).
4. BotFather replies with a **token** like `123456789:AAExxxxxxxxxxxxxxxxxxx`.
   → **Secret #1: `TELEGRAM_BOT_TOKEN`**

Optional but recommended — lock the bot to just you:

5. Search for **@userinfobot**, press Start. It shows your numeric user ID.
   → **Secret #7: `ALLOWED_TELEGRAM_USER_IDS`** (just your number)

---

## Part 2 — Get an Anthropic API key (5 min)

This is what reads your screenshots.

1. Go to **https://console.anthropic.com** and sign up / log in.
2. Add a payment method under **Billing** (reading screenshots costs a fraction
   of a cent each on the default model).
3. Go to **API Keys → Create Key**, name it, and copy it (starts with `sk-ant-`).
   → **Secret #3: `ANTHROPIC_API_KEY`**

> Model choice: the default is `claude-sonnet-5` (great accuracy, low cost).
> To go cheaper set `ANTHROPIC_MODEL=claude-haiku-4-5`; for maximum accuracy use
> `claude-opus-4-8`.

---

## Part 3 — Create the Google Sheet + service account (10 min)

Your trades are stored in a Google Sheet you can also open and edit by hand.

### 3a. Create the sheet
1. Go to **https://sheets.google.com** and create a **blank spreadsheet**.
2. Name it e.g. *Trading Journal*.
3. Look at the URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_LONG_ID`**`/edit`
   Copy that middle part. → **Secret #5: `GOOGLE_SHEET_ID`**

### 3b. Create a service account (a robot Google account for the bot)
1. Go to **https://console.cloud.google.com** and, at the top, create a new
   project (e.g. *trading-journal*).
2. Enable the Sheets API: search **"Google Sheets API"** in the top search bar →
   open it → **Enable**.
3. In the left menu go to **APIs & Services → Credentials**.
4. Click **Create Credentials → Service account**. Give it a name, click
   **Create and continue**, then **Done** (skip the optional steps).
5. Click the new service account → **Keys** tab → **Add key → Create new key →
   JSON**. A `.json` file downloads. **Open it in a text editor.**
   → **Secret #6: `GOOGLE_CREDENTIALS_JSON`** — this is the *entire contents* of
   that file.

### 3c. Share the sheet with the robot
1. In that JSON file, find the line `"client_email": "...@...iam.gserviceaccount.com"`.
   Copy that email.
2. Back in your Google Sheet, click **Share** and paste that email, give it
   **Editor** access, and send. (No notification needed.)

That's it — the bot can now write to your sheet.

---

## Part 4 — Invent a webhook secret

Any random string works. It secures the URL Telegram posts to.
Run this locally, or just make one up (letters/numbers, no spaces):
```
python -c "import secrets; print(secrets.token_urlsafe(24))"
```
→ **Secret #2: `WEBHOOK_SECRET`**

---

## Part 5 — Deploy on Render (5 min)

1. Push this repo to your GitHub (already done if you're reading this there).
2. Go to **https://render.com**, sign up, and connect your GitHub.
3. Click **New + → Blueprint**, pick this repository. Render reads `render.yaml`
   and creates the web service.
4. It will ask you to fill in the secret env vars. Paste each one:
   - `TELEGRAM_BOT_TOKEN` (Secret #1)
   - `WEBHOOK_SECRET` (Secret #2)
   - `ANTHROPIC_API_KEY` (Secret #3)
   - `GOOGLE_SHEET_ID` (Secret #5)
   - `GOOGLE_CREDENTIALS_JSON` (Secret #6 — paste the whole JSON)
   - `ALLOWED_TELEGRAM_USER_IDS` (Secret #7 — or leave blank)
5. Click **Apply / Create**. Wait for the build to go green.
6. Render gives your service a URL like `https://trading-journal.onrender.com`.
   The app **registers the Telegram webhook automatically on startup** using
   that URL — nothing more to do.

### Verify
- Open your Render URL in a browser → you should see the (empty) dashboard.
- In Telegram, open your bot and send `/start`.
- Send a screenshot of any trade → within a few seconds you get a confirmation,
  and the trade appears on the dashboard and in your Google Sheet.

---

## Notes on Render's free tier
The free web service **sleeps after ~15 min of no traffic**. Because we use
webhook mode, the next screenshot you send wakes it automatically (Telegram
retries delivery), so nothing is lost — the first message after a nap may just
take a few extra seconds. If you want it always-on, upgrade to Render's paid
tier or add a free uptime pinger (e.g. UptimeRobot) hitting `/health`.

## Troubleshooting
- **Bot doesn't reply:** open Render → Logs. Check for "setWebhook -> True".
  Visit `/health` — it lists any missing config.
- **"could not read journal":** the sheet isn't shared with the service-account
  email, or `GOOGLE_SHEET_ID` is wrong.
- **Wrong numbers extracted:** send a clearer/cropped screenshot, or switch
  `ANTHROPIC_MODEL` to `claude-opus-4-8` for higher accuracy. Use `/undo` to
  remove a bad entry.
