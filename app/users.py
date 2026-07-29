"""User resolution for dashboard login and Telegram-bot routing.

A "user" is a dict: {username, telegram, sheet_id, role}. Two sources:
1. The bootstrap owner from env (OWNER_USERNAME/PASSWORD/OWNER_TELEGRAM_USERNAME),
   always an admin, whose trades live in the control sheet.
2. Rows in the control sheet's Users tab.
"""
from __future__ import annotations

from typing import Any

from . import auth, config, storage


def _owner_user() -> dict[str, Any] | None:
    if not config.OWNER_USERNAME:
        return None
    return {
        "username": config.OWNER_USERNAME,
        "telegram": config.OWNER_TELEGRAM_USERNAME,
        "sheet_id": config.GOOGLE_SHEET_ID,  # owner's trades stay in the control sheet
        "role": "admin",
    }


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "username": str(row.get("username", "")),
        "telegram": str(row.get("telegram", "")).lstrip("@"),
        "sheet_id": str(row.get("sheet_id", "")) or config.GOOGLE_SHEET_ID,
        "role": str(row.get("role", "user")) or "user",
    }


async def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """Verify dashboard login credentials; return the user or None."""
    username = (username or "").strip()
    owner = _owner_user()
    if owner and username.lower() == config.OWNER_USERNAME.lower():
        return owner if password == config.OWNER_PASSWORD else None

    row = await storage.get_user_by_login(username)
    if row and auth.verify_password(password, str(row.get("password_hash", ""))):
        return _public(row)
    return None


async def by_telegram(telegram: str) -> dict[str, Any] | None:
    """Resolve a Telegram username to a registered user (for bot routing)."""
    tg = (telegram or "").strip().lstrip("@").lower()
    if not tg:
        return None
    owner = _owner_user()
    if owner and owner["telegram"] and tg == owner["telegram"]:
        return owner
    row = await storage.get_user_by_telegram(tg)
    return _public(row) if row else None


async def is_admin_telegram(telegram: str) -> bool:
    u = await by_telegram(telegram)
    return bool(u and u.get("role") == "admin")


async def all_users() -> list[dict[str, Any]]:
    """Every user (owner + registry rows), for the admin dashboard switcher."""
    out: list[dict[str, Any]] = []
    owner = _owner_user()
    if owner:
        out.append(owner)
    for row in await storage.list_users():
        if not row.get("username"):
            continue
        # Skip a duplicate of the owner row if it was also added to the sheet.
        if owner and str(row.get("username", "")).lower() == owner["username"].lower():
            continue
        out.append(_public(row))
    return out


class UserError(ValueError):
    """A user-facing validation error for the admin API to surface."""


def _is_owner_username(username: str) -> bool:
    return bool(config.OWNER_USERNAME) and username.strip().lower() == config.OWNER_USERNAME.lower()


async def create_user(
    username: str, password: str, telegram: str, sheet_id: str, role: str = "user",
) -> dict[str, Any]:
    username = (username or "").strip()
    if not username or not password:
        raise UserError("Username and password are required.")
    if _is_owner_username(username):
        raise UserError("That username is reserved for the owner account.")
    if await storage.get_user_by_login(username):
        raise UserError(f"'{username}' already exists.")
    if role not in ("user", "admin"):
        role = "user"
    await storage.add_user(username, auth.hash_password(password), telegram or "", sheet_id or "", role)
    return _public({
        "username": username, "telegram": telegram, "sheet_id": sheet_id, "role": role,
    })


async def update_user(
    username: str, password: str = "", telegram: str | None = None,
    sheet_id: str | None = None, role: str | None = None,
) -> None:
    if _is_owner_username(username):
        raise UserError("The owner account is configured via environment variables, not here.")
    existing = await storage.get_user_by_login(username)
    if not existing:
        raise UserError(f"'{username}' was not found.")
    fields: dict[str, Any] = {}
    if password:
        fields["password_hash"] = auth.hash_password(password)
    if telegram is not None:
        fields["telegram"] = telegram.lstrip("@")
    if sheet_id is not None:
        fields["sheet_id"] = sheet_id
    if role is not None and role in ("user", "admin"):
        fields["role"] = role
    if fields and not await storage.update_user(username, fields):
        raise UserError(f"'{username}' was not found.")


async def delete_user(username: str) -> None:
    if _is_owner_username(username):
        raise UserError("The owner account cannot be deleted.")
    if not await storage.delete_user(username):
        raise UserError(f"'{username}' was not found.")
