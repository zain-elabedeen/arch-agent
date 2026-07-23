"""Hosted WorkOS AuthKit session routes."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from agent.app.api.contracts import CsrfTokenResponse, LogoutResponse, OrganizationSwitchResponse, SignedOutResponse
from agent.app.config import Settings, get_settings
from agent.app.logging_utils import get_logger
from agent.app.product.auth import (
    SESSION_COOKIE,
    Identity,
    ensure_self_serve_organization,
    identity_from_workos_response,
    require_identity,
)
from agent.app.product.csrf import CSRF_COOKIE, issue_csrf_token, require_csrf
from agent.app.product.store import ProductStore, get_product_store
from agent.app.product.workos_client import get_field, get_workos_client

router = APIRouter(tags=["Auth"])
logger = get_logger("agent.api.auth")


class OrganizationSwitch(BaseModel):
    organization_id: str = Field(description="Organization id to make active in the sealed WorkOS session.")


def set_session_cookie(response: Response, sealed_session: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        sealed_session,
        max_age=settings.session_cookie_max_age_sec,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
        path="/",
    )


def delete_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
        path="/",
    )
    response.delete_cookie(
        CSRF_COOKIE,
        secure=settings.cookie_secure,
        httponly=False,
        samesite=settings.cookie_samesite,
        path="/",
    )


def _url_origin(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _redirect_origin_allowed(origin: str, settings: Settings) -> bool:
    allowed_origins = set(settings.allowed_origin_list)
    for configured_url in (settings.workos_post_login_redirect, settings.workos_sign_out_redirect):
        configured_origin = _url_origin(configured_url)
        if configured_origin:
            allowed_origins.add(configured_origin)
    if origin in allowed_origins:
        return True
    return bool(settings.allowed_origin_regex and re.fullmatch(settings.allowed_origin_regex, origin))


def _safe_frontend_redirect(candidate: str | None, *, default_url: str, settings: Settings) -> str:
    if not candidate or not candidate.strip():
        return default_url
    raw_url = candidate.strip()
    if raw_url.startswith("/") and not raw_url.startswith("//"):
        default = urlsplit(default_url)
        if not default.scheme or not default.netloc:
            return default_url
        raw_url = urlunsplit((default.scheme, default.netloc, raw_url, "", ""))

    parsed = urlsplit(raw_url)
    origin = _url_origin(raw_url)
    if not origin or not _redirect_origin_allowed(origin, settings):
        return default_url
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, parsed.fragment))


def _request_frontend_redirect(
    request: Request,
    candidate: str | None,
    *,
    default_url: str,
    settings: Settings,
) -> str:
    if candidate:
        return _safe_frontend_redirect(candidate, default_url=default_url, settings=settings)

    referer_redirect = _safe_frontend_redirect(
        request.headers.get("referer"),
        default_url=default_url,
        settings=settings,
    )
    if referer_redirect != default_url:
        return referer_redirect

    origin = request.headers.get("origin")
    origin_value = _url_origin(origin or "")
    if origin_value and _redirect_origin_allowed(origin_value, settings):
        default = urlsplit(default_url)
        origin_parts = urlsplit(origin_value)
        return urlunsplit(
            (
                origin_parts.scheme,
                origin_parts.netloc,
                default.path or "/",
                default.query,
                default.fragment,
            )
        )

    return default_url


@router.get(
    "/auth/login",
    summary="Start WorkOS Login",
    description="Redirect the browser to WorkOS AuthKit hosted login.",
)
def login(
    return_to: str | None = Query(None, description="Allowed frontend URL to return to after authentication."),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    state = _safe_frontend_redirect(return_to, default_url=settings.workos_post_login_redirect, settings=settings)
    return RedirectResponse(get_workos_client().authorization_url(state=state), status_code=302)


@router.get(
    "/auth/callback",
    summary="Complete WorkOS Login",
    description="Exchange a WorkOS authorization code, mirror the identity locally, set the sealed session cookie, and redirect to the frontend.",
)
def callback(
    code: str,
    state: str | None = Query(None, description="Frontend return URL previously passed through the WorkOS OAuth state."),
    store: ProductStore = Depends(get_product_store),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    client = get_workos_client()
    response = client.authenticate_with_code(code)
    sealed_session = get_field(response, "sealed_session", "sealedSession")
    if not sealed_session:
        raise HTTPException(status_code=401, detail="WorkOS did not return a sealed session.")
    identity = identity_from_workos_response(response, store, settings)
    ensure_self_serve_organization(identity, store, settings, client=client)
    redirect_to = _safe_frontend_redirect(state, default_url=settings.workos_post_login_redirect, settings=settings)
    redirect = RedirectResponse(redirect_to, status_code=302)
    set_session_cookie(redirect, str(sealed_session), settings)
    return redirect


@router.get(
    "/auth/csrf",
    response_model=CsrfTokenResponse,
    summary="Issue CSRF Token",
    description="Issue a signed double-submit CSRF token and set the matching readable CSRF cookie.",
)
def csrf(settings: Settings = Depends(get_settings)) -> JSONResponse:
    token = issue_csrf_token()
    response = JSONResponse({"csrf_token": token})
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=settings.csrf_token_ttl_sec,
        secure=settings.cookie_secure,
        httponly=False,
        samesite=settings.cookie_samesite,
        path="/",
    )
    return response


@router.post(
    "/auth/logout",
    response_model=None,
    dependencies=[Depends(require_csrf)],
    responses={200: {"model": LogoutResponse, "description": "Logout URL payload for fetch-based clients."}},
    summary="Log Out",
    description="Clear the ArchAgent session and CSRF cookies, then redirect through the WorkOS logout URL.",
)
def logout(
    request: Request,
    return_to: str | None = Query(None, description="Allowed frontend URL to return to after logout."),
    response_format: Literal["redirect", "json"] = Query(
        "redirect",
        alias="format",
        description="Use json when logout is initiated by fetch and the frontend will navigate to logout_url.",
    ),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse | JSONResponse:
    frontend_return_to = _request_frontend_redirect(
        request,
        return_to,
        default_url=settings.workos_sign_out_redirect,
        settings=settings,
    )
    logout_url = frontend_return_to
    sealed_session = request.cookies.get(SESSION_COOKIE)
    if sealed_session:
        try:
            session = get_workos_client().load_session(sealed_session)
            logout_url = session.get_logout_url(return_to=frontend_return_to)
        except Exception as exc:
            logger.warning("workos logout URL unavailable; clearing local cookies only error=%s", exc)
    if response_format == "json":
        response = JSONResponse({"logout_url": logout_url, "redirect_to": frontend_return_to})
    else:
        response = RedirectResponse(logout_url, status_code=302)
    delete_auth_cookies(response, settings)
    return response


@router.get(
    "/auth/signed-out",
    response_model=SignedOutResponse,
    summary="Signed-Out Confirmation",
    description="Small success payload used after the browser returns from WorkOS logout.",
)
def signed_out() -> dict[str, bool]:
    return {"signed_out": True}


@router.post(
    "/v1/session/organization",
    response_model=OrganizationSwitchResponse,
    dependencies=[Depends(require_csrf)],
    tags=["Product"],
    summary="Switch Active Organization",
    description="Refresh the sealed WorkOS session for another organization available to the current user.",
)
def switch_organization(
    payload: OrganizationSwitch,
    request: Request,
    _: Identity = Depends(require_identity),
    store: ProductStore = Depends(get_product_store),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    session = get_workos_client().load_session(request.cookies.get(SESSION_COOKIE))
    result = session.refresh(organization_id=payload.organization_id)
    if not get_field(result, "authenticated", default=False):
        raise HTTPException(status_code=403, detail="Organization switch was rejected.")
    identity = identity_from_workos_response(result, store, settings)
    sealed_session = get_field(result, "sealed_session", "sealedSession")
    response = JSONResponse(
        {
            "organization_id": identity.organization_id,
            "workos_organization_id": identity.workos_organization_id,
            "role": identity.role,
            "permissions": list(identity.permissions),
        }
    )
    if sealed_session:
        set_session_cookie(response, str(sealed_session), settings)
    return response
