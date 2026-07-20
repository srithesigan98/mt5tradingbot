"""FastAPI app: Telegram webhook receiver + live trading-journal dashboard."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import bot, config, storage
from .stats import compute

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

WEBHOOK_PATH = f"/telegram/webhook/{config.WEBHOOK_SECRET}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = config.missing_required()
    if missing:
        log.warning("Missing required env vars: %s. The bot will not fully work.", ", ".join(missing))

    # Register the webhook with Telegram so it pushes updates to us.
    if config.TELEGRAM_BOT_TOKEN and config.PUBLIC_URL:
        from . import telegram

        url = config.PUBLIC_URL.rstrip("/") + WEBHOOK_PATH
        try:
            result = await telegram.set_webhook(url)
            log.info("setWebhook -> %s (%s)", result.get("ok"), url)
        except Exception:  # noqa: BLE001
            log.exception("Failed to set Telegram webhook")
    else:
        log.warning("PUBLIC_URL not set yet; skipping webhook registration.")

    yield


app = FastAPI(title="Trading Journal", lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "missing_config": config.missing_required()})


@app.post(WEBHOOK_PATH)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> JSONResponse:
    # Telegram echoes back the secret token we set; reject anything else.
    if x_telegram_bot_api_secret_token != config.WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="invalid secret token")

    update = await request.json()
    # Process in the background so Telegram gets an immediate 200 and doesn't retry.
    asyncio.create_task(bot.handle_update(update))
    return JSONResponse({"ok": True})


@app.get("/api/trades")
async def api_trades() -> JSONResponse:
    try:
        rows = await storage.read_trades()
    except Exception:  # noqa: BLE001
        log.exception("Failed to read trades")
        raise HTTPException(status_code=500, detail="could not read journal")
    return JSONResponse(compute(rows))


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    return _TEMPLATES.TemplateResponse(request, "dashboard.html", {})
