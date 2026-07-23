"""Stateless double-submit CSRF protection for browser mutations."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request

from agent.app.config import get_settings

CSRF_COOKIE = "__Host-archagent-csrf"
CSRF_HEADER = "x-archagent-csrf"
def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _signature(payload: str, secret: str) -> str:
    return _encode(hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())


def issue_csrf_token() -> str:
    secret = get_settings().csrf_secret
    if not secret:
        if get_settings().environment == "prod":
            raise RuntimeError("ARCHAGENT_CSRF_SECRET is required in production.")
        secret = "archagent-development-csrf-secret"
    payload = f"{int(time.time())}.{secrets.token_urlsafe(24)}"
    return f"{payload}.{_signature(payload, secret)}"


def validate_csrf_token(token: str) -> None:
    secret = get_settings().csrf_secret
    if not secret:
        if get_settings().environment == "prod":
            raise HTTPException(status_code=500, detail="CSRF protection is not configured.")
        secret = "archagent-development-csrf-secret"
    try:
        timestamp, nonce, signature = token.split(".", 2)
        issued_at = int(timestamp)
    except (TypeError, ValueError):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.") from None
    payload = f"{timestamp}.{nonce}"
    if not hmac.compare_digest(signature, _signature(payload, secret)):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")
    if issued_at > int(time.time()) + 30 or int(time.time()) - issued_at > get_settings().csrf_token_ttl_sec:
        raise HTTPException(status_code=403, detail="Expired CSRF token.")


def require_csrf(request: Request) -> None:
    """Require matching signed cookie/header tokens for unsafe hosted requests."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    settings = get_settings()
    if settings.auth_mode != "workos":
        return
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    header_token = request.headers.get(CSRF_HEADER, "")
    if not cookie_token or not hmac.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=403, detail="CSRF token is required.")
    validate_csrf_token(cookie_token)
