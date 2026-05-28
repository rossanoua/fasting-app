"""HTTP sync endpoint for Mini App ↔ bot integration.

Mini App POSTs to /api/sync when user starts/stops a fast. Bot validates
Telegram initData (HMAC-SHA256 per Telegram docs), updates SQLite,
auto-schedules reminders via the existing scheduler.

Routes:
    POST /api/sync   {action: "start"|"stop", protocol: "16"|"18"|"20"|"omad"}
    GET  /health     {ok: true}                — for tunnel monitoring
    OPTIONS /api/sync                          — CORS preflight

initData validation reference:
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Optional
from urllib.parse import unquote

from aiohttp import web

import db

log = logging.getLogger("fasting-bot.sync")

# Same protocol map as bot.py — duplicated to keep sync_api a leaf module.
PROTOCOLS = {
    "16": (16, 8),
    "18": (18, 6),
    "20": (20, 4),
    "omad": (23, 1),
}


def validate_init_data(init_data: str, bot_token: str) -> Optional[dict]:
    """Validates Telegram Mini App initData. Returns parsed user dict
    on success, None on any failure (bad hash, malformed, expired)."""
    try:
        # Parse query-string format: "key1=val1&key2=val2&hash=..."
        parts = init_data.split("&")
        parsed = {}
        for p in parts:
            if "=" not in p:
                continue
            k, v = p.split("=", 1)
            parsed[k] = unquote(v)

        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None

        # Reject if too old (>1 hour) — initData has `auth_date` (unix seconds)
        auth_date = int(parsed.get("auth_date", "0"))
        if auth_date and (time.time() - auth_date) > 3600:
            log.warning(f"initData too old: auth_date={auth_date}")
            return None

        # Build data_check_string: keys sorted alphabetically, joined by \n
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(parsed.items())
        )

        # secret_key = HMAC-SHA256(key=b"WebAppData", msg=bot_token)
        secret_key = hmac.new(
            b"WebAppData", bot_token.encode(), hashlib.sha256
        ).digest()
        computed_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            log.warning("initData hash mismatch")
            return None

        user_raw = parsed.get("user")
        if not user_raw:
            return None
        return json.loads(user_raw)
    except Exception as e:
        log.warning(f"initData validation error: {e}")
        return None


def cors_headers() -> dict:
    """CORS headers — Mini App fetches from a different origin (GitHub Pages)."""
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data",
        "Access-Control-Max-Age": "86400",
    }


async def handle_health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True}, headers=cors_headers())


async def handle_cors_preflight(_request: web.Request) -> web.Response:
    return web.Response(status=204, headers=cors_headers())


def make_sync_handler(bot_token: str, now_ts_fn=None):
    """Closure-based handler factory so bot_token doesn't live as a module global.
    now_ts_fn injectable for testing."""
    if now_ts_fn is None:
        now_ts_fn = lambda: int(time.time())

    async def handle_sync(request: web.Request) -> web.Response:
        # 1. Auth
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        if not init_data:
            return web.json_response(
                {"ok": False, "error": "missing X-Telegram-Init-Data"},
                status=401, headers=cors_headers(),
            )

        user = validate_init_data(init_data, bot_token)
        if not user:
            return web.json_response(
                {"ok": False, "error": "invalid initData"},
                status=401, headers=cors_headers(),
            )

        user_id = user.get("id")
        if not user_id:
            return web.json_response(
                {"ok": False, "error": "no user id in initData"},
                status=400, headers=cors_headers(),
            )

        # 2. Parse body
        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {"ok": False, "error": "invalid JSON body"},
                status=400, headers=cors_headers(),
            )

        action = body.get("action")
        if action == "start":
            protocol = str(body.get("protocol", "")).lower()
            if protocol not in PROTOCOLS:
                return web.json_response(
                    {"ok": False, "error": f"unknown protocol: {protocol}"},
                    status=400, headers=cors_headers(),
                )
            target_h, eating_h = PROTOCOLS[protocol]
            # NOTE: chat_id == user_id for private chats with bot
            await db.start_fast(user_id, user_id, target_h, eating_h, now_ts_fn())
            log.info(f"sync start: user={user_id} proto={protocol}")
            return web.json_response(
                {"ok": True, "synced": "start", "protocol": protocol,
                 "target_hours": target_h, "eating_hours": eating_h},
                headers=cors_headers(),
            )

        if action == "stop":
            await db.stop_all(user_id)
            log.info(f"sync stop: user={user_id}")
            return web.json_response(
                {"ok": True, "synced": "stop"},
                headers=cors_headers(),
            )

        return web.json_response(
            {"ok": False, "error": f"unknown action: {action}"},
            status=400, headers=cors_headers(),
        )

    return handle_sync


def build_app(bot_token: str) -> web.Application:
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_post("/api/sync", make_sync_handler(bot_token))
    app.router.add_options("/api/sync", handle_cors_preflight)
    return app
