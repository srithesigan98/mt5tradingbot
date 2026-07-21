"""FastAPI app: Telegram webhook receiver + live trading-journal dashboard."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import bot, config, storage
from .stats import compute, filter_by_trader, trader_names

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
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


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
async def api_trades(trader: str = Query(default="")) -> JSONResponse:
    try:
        rows = await storage.read_trades()
    except Exception:  # noqa: BLE001
        log.exception("Failed to read trades")
        raise HTTPException(status_code=500, detail="could not read journal")

    traders = trader_names(rows)
    selected = trader.strip()
    if selected:
        rows = filter_by_trader(rows, selected)

    payload = compute(rows)
    payload["traders"] = traders
    payload["selected_trader"] = selected
    return JSONResponse(payload)


# Small in-memory cache so repeat views don't re-download from Telegram.
_image_cache: dict[str, bytes] = {}
_IMAGE_CACHE_MAX = 60


@app.get("/api/image/{file_id}")
async def api_image(file_id: str) -> Response:
    """Serve a trade screenshot by proxying it from Telegram's file storage."""
    if file_id in _image_cache:
        return Response(content=_image_cache[file_id], media_type="image/jpeg")
    try:
        from . import telegram

        data = await telegram.get_file_bytes(file_id)
    except Exception:  # noqa: BLE001
        log.exception("Failed to fetch image %s", file_id)
        raise HTTPException(status_code=404, detail="image not available")

    if len(_image_cache) >= _IMAGE_CACHE_MAX:
        _image_cache.pop(next(iter(_image_cache)))
    _image_cache[file_id] = data
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/news")
async def api_news() -> JSONResponse:
    from . import news
    events = await news.upcoming(limit=15)
    return JSONResponse({"events": events, "embed_url": config.ECON_CALENDAR_EMBED_URL})


@app.api_route("/cron/news/{secret}", methods=["GET", "POST"])
async def cron_news(secret: str) -> JSONResponse:
    """Hit this every ~5 min from a free cron pinger to fire due news alerts."""
    if secret != config.WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="invalid secret")
    from . import news
    try:
        result = await news.run_check()
    except Exception as exc:  # noqa: BLE001 — keep the pinger from seeing 500s
        log.exception("news check failed")
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return JSONResponse(result)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    return _TEMPLATES.TemplateResponse(request, "dashboard.html", {})
