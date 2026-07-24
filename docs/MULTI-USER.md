# Multi-user setup (login + one bot, per-user databases)

One deployment, one Telegram bot, many users. Each user logs into the dashboard
with their own password and sees only their own trades; the bot logs each
person's trades into **their own Google Sheet** based on their Telegram username.

## How it works

- Your existing sheet (`GOOGLE_SHEET_ID`) is the **control sheet**: it holds the
  `Users` registry, the shared news tabs, and **your** (the owner's) trades.
- Each additional user gets **their own Google Sheet** (their private database).
- The bot only logs a trade if the sender's Telegram **@username** is registered,
  and routes it to that user's sheet.
- The dashboard requires login. You (admin) can switch between users; everyone
  else sees only themselves.

## One-time: enable your own login (REQUIRED)

⚠️ After this update the dashboard needs a login, so set these in Render →
your service → **Environment**, or nobody (including you) can get in:

| Variable | Value |
|---|---|
| `OWNER_USERNAME` | the username you'll log in with (e.g. `sri`) |
| `OWNER_PASSWORD` | your dashboard password |
| `OWNER_TELEGRAM_USERNAME` | your Telegram @username, **without the @** (e.g. `srithesigan`) |

Your existing trades stay in the control sheet and remain yours — no migration.
Make sure your Telegram account has a **@username** set (Telegram → Settings),
otherwise the bot can't match you.

Then open your dashboard URL → you'll get a login screen → use OWNER_USERNAME /
OWNER_PASSWORD.

## Adding another user

For each new person:

1. **Create a new Google Sheet** for them (blank).
2. **Share** it with your service-account email (the `...iam.gserviceaccount.com`
   from your Google credentials) as **Editor**.
3. Copy the new sheet's **ID** (the long string in its URL between `/d/` and `/edit`).
4. In Telegram (as the owner), send the bot:
   ```
   /adduser <login> <password> <their_telegram_username> <sheet_id>
   ```
   e.g. `/adduser john johnspw john_fx 1AbCd...XyZ`
5. **Delete that message** afterwards — it contains their password.

That person can now:
- Message the bot (with their @username set) to log trades → they go to their sheet.
- Log into the dashboard at your URL with `login` / `password` → they see only
  their own journal.

You can also add/edit users by hand in the **Users** tab of the control sheet
(columns: username, password_hash, telegram, sheet_id, role) — but passwords
there must be pre-hashed, so `/adduser` is easier.

## Notes

- **Admin view:** when you log in, tabs across the top let you switch between
  every user's dashboard. Normal users don't see the switcher.
- **News alerts & the gold outlook** are shared (global) — all subscribed users
  get them regardless of which sheet their trades live in.
- **Roles:** `/adduser` creates `user` rows. To make someone else an admin, set
  their `role` to `admin` in the Users tab.
