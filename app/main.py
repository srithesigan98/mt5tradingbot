"""FastAPI app: Telegram webhook receiver + live trading-journal dashboard."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, bot, config, storage, users
from .stats import compute, filter_by_trader, trader_names

COOKIE = "tj_session"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

WEBHOOK_PATH = f"/telegram/webhook/{config.WEBHOOK_SECRET}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = config.missing_required()
    if missing:
        log.warning("Missing required env vars: %s. The bot will not fully work.", ", ".join(missing))
    if not config.OWNER_USERNAME or not config.OWNER_PASSWORD:
        log.warning(
            "OWNER_USERNAME / OWNER_PASSWORD not set — nobody can log into the "
            "dashboard. Set them (plus OWNER_TELEGRAM_USERNAME) in your environment."
        )

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


def _current_user(request: Request) -> dict | None:
    return auth.read_session(request.cookies.get(COOKIE))


def _require_user(request: Request) -> dict:
    u = _current_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="login required")
    return u


@app.get("/api/trades")
async def api_trades(
    request: Request,
    user: str = Query(default=""),
    trader: str = Query(default=""),
) -> JSONResponse:
    me = _require_user(request)

    # Which user's sheet to show. Admins may view any user via ?user=<username>.
    target = me
    viewers: list[str] = [me["username"]]
    if me.get("role") == "admin":
        everyone = await users.all_users()
        viewers = [u["username"] for u in everyone]
        if user:
            match = next((u for u in everyone if u["username"].lower() == user.lower()), None)
            if match:
                target = match

    try:
        rows = await storage.read_trades(target["sheet_id"])
    except Exception:  # noqa: BLE001
        log.exception("Failed to read trades")
        raise HTTPException(status_code=500, detail="could not read journal")

    # Within the selected sheet, allow viewing a specific trader's profile.
    traders = trader_names(rows)
    selected_trader = trader.strip()
    if selected_trader:
        rows = filter_by_trader(rows, selected_trader)

    payload = compute(rows)
    payload["me"] = {"username": me["username"], "role": me.get("role", "user")}
    payload["viewers"] = viewers
    payload["selected_user"] = target["username"]
    payload["traders"] = traders
    payload["selected_trader"] = selected_trader
    return JSONResponse(payload)


# Small in-memory cache so repeat views don't re-download from Telegram.
_image_cache: dict[str, bytes] = {}
_IMAGE_CACHE_MAX = 60


@app.get("/api/image/{file_id}")
async def api_image(request: Request, file_id: str) -> Response:
    """Serve a trade screenshot by proxying it from Telegram's file storage."""
    _require_user(request)
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
    payload = await news.calendar_payload(limit=15)
    return JSONResponse(payload)


@app.get("/api/news/analyses")
async def api_news_analyses() -> JSONResponse:
    try:
        rows = await storage.read_analyses(limit=20)
    except Exception:  # noqa: BLE001
        log.exception("Failed to read news analyses")
        rows = []
    return JSONResponse({"analyses": rows})


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


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = Query(default="")) -> HTMLResponse:
    if _current_user(request):
        return RedirectResponse("/", status_code=303)
    return _TEMPLATES.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
async def login_submit(username: str = Form(...), password: str = Form(...)) -> Response:
    user = await users.authenticate(username, password)
    if not user:
        return RedirectResponse("/login?error=1", status_code=303)
    token = auth.make_session({
        "username": user["username"], "role": user.get("role", "user"),
        "sheet_id": user["sheet_id"], "telegram": user.get("telegram", ""),
    })
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        COOKIE, token, max_age=config.SESSION_DAYS * 86400,
        httponly=True, samesite="lax", secure=bool(config.PUBLIC_URL.startswith("https")),
    )
    return resp


@app.get("/logout")
async def logout() -> Response:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> Response:
    if not _current_user(request):
        return RedirectResponse("/login", status_code=303)
    return _TEMPLATES.TemplateResponse(request, "dashboard.html", {})
