# Spinning up a second (fully separate) account

Your app is single-tenant by config: everything that defines an "account" — which
Telegram bot, which Google Sheet (database), which dashboard — comes from
environment variables. So a completely isolated second account needs **no code
changes**: you just deploy the **same repo** again with different values.

Each instance shares nothing but the code, and because both deploy from the same
branch, any future improvement updates both automatically.

> If instead you want **one** site + **one** bot serving many users who each log
> in and see only their own data, that's the multi-tenant/login approach — a
> different design (see the app's login feature), and it avoids running multiple
> Render services.

## Reuse vs. create new

| Setting | Second account |
|---|---|
| GitHub repo / code | ♻️ Same — no fork |
| `TELEGRAM_BOT_TOKEN` | 🆕 New bot from @BotFather |
| `GOOGLE_SHEET_ID` | 🆕 New blank Google Sheet (its own database) |
| `GOOGLE_CREDENTIALS_JSON` | ♻️ Reuse the same service account — just **Share** the new sheet with that `...iam.gserviceaccount.com` email as Editor |
| `WEBHOOK_SECRET` | 🆕 New random string |
| `ANTHROPIC_API_KEY` | Reuse (shared credits) or new (separate billing) |
| `UTC_OFFSET_HOURS`, `ANTHROPIC_MODEL`, news settings | ♻️ Same (or customize) |

## Steps (~15 min)

1. **New Telegram bot** — @BotFather → `/newbot` → copy the token.
2. **New Google Sheet** — create it, copy the ID from the URL, and **Share** it
   with your existing service-account email (Editor). Reusing the service account
   means no new Google Cloud setup.
3. **New Render Web Service** — Render → **New → Web Service** → pick the **same
   repo** → set the env vars above (new bot token, new sheet ID, new webhook
   secret; reuse the Google JSON + Anthropic key).
4. **New cron pinger** — a second cron-job.org job hitting the new service's
   `https://NEW-URL.onrender.com/cron/news/NEW_SECRET` every 5 minutes.
5. The new user opens their bot, sends `/start`, and has their own private
   journal, dashboard, and news alerts.

The two instances are fully isolated — separate bots, separate databases,
separate dashboards.
