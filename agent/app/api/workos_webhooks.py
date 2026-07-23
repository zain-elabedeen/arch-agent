"""Signed WorkOS webhook ingress."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from agent.app.api.contracts import WorkOSWebhookResponse
from agent.app.config import Settings, get_settings
from agent.app.logging_utils import get_logger
from agent.app.product.store import ProductStore, get_product_store
from agent.app.product.tasks import get_task_dispatcher
from agent.app.product.workos_client import get_field, get_workos_client
from agent.app.product.workos_sync import process_workos_event

router = APIRouter(tags=["Webhooks"])
logger = get_logger("agent.api.workos_webhooks")


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else "empty"


def _signature_diagnostics(body: bytes, signature: str, secret: str | None) -> dict[str, Any]:
    parts = {}
    for item in signature.split(","):
        key, _, value = item.strip().partition("=")
        if key and value:
            parts[key] = value
    timestamp = parts.get("t")
    signature_hash = parts.get("v1")
    diagnostics: dict[str, Any] = {
        "header_has_timestamp": bool(timestamp),
        "header_has_v1": bool(signature_hash),
        "secret_configured": bool(secret),
        "secret_fingerprint": _fingerprint(secret or ""),
    }
    if not timestamp or not signature_hash or not secret:
        return diagnostics
    try:
        timestamp_ms = int(timestamp)
    except ValueError:
        diagnostics["timestamp_parseable"] = False
        return diagnostics
    body_text = body.decode("utf-8", errors="replace")
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{body_text}".encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
    diagnostics.update(
        {
            "timestamp_parseable": True,
            "timestamp_age_sec": round(time.time() - (timestamp_ms / 1000), 3),
            "signature_match": hmac.compare_digest(signature_hash, expected_signature),
        }
    )
    return diagnostics


def _event_payload(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return event
    if hasattr(event, "model_dump"):
        return event.model_dump(mode="json")
    if hasattr(event, "dict"):
        return event.dict()
    if hasattr(event, "to_dict"):
        return event.to_dict()
    return {"id": get_field(event, "id"), "event": get_field(event, "event", "type")}


@router.post(
    "/webhooks/workos/{path_token}",
    response_model=WorkOSWebhookResponse,
    summary="Receive WorkOS Webhook",
    description="Validate a signed WorkOS webhook, persist its receipt idempotently, and enqueue asynchronous synchronization.",
)
async def receive_workos_webhook(
    path_token: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    store: ProductStore = Depends(get_product_store),
) -> dict[str, bool]:
    if not settings.workos_webhook_path_token or path_token != settings.workos_webhook_path_token:
        logger.warning(
            "workos webhook rejected reason=invalid_path_token configured=%s path_token_fingerprint=%s client=%s",
            bool(settings.workos_webhook_path_token),
            _fingerprint(path_token),
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=404, detail="Webhook not found.")
    signature = request.headers.get("workos-signature")
    if not signature:
        logger.warning(
            "workos webhook rejected reason=missing_signature content_type=%s client=%s",
            request.headers.get("content-type", ""),
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=400, detail="Missing WorkOS signature.")
    body = await request.body()
    try:
        event = get_workos_client().construct_webhook_event(body, signature)
    except Exception as exc:
        diagnostics = _signature_diagnostics(body, signature, settings.workos_webhook_secret)
        logger.warning(
            "workos webhook rejected reason=invalid_signature body_bytes=%d signature_bytes=%d error_type=%s error=%s diagnostics=%s client=%s",
            len(body),
            len(signature),
            type(exc).__name__,
            str(exc),
            diagnostics,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=400, detail="Invalid WorkOS signature.") from None
    payload = _event_payload(event)
    event_id = str(get_field(event, "id", default=payload.get("id") or ""))
    event_type = str(get_field(event, "event", "type", default=payload.get("event") or "unknown"))
    if not event_id:
        logger.warning(
            "workos webhook rejected reason=missing_event_id event_type=%s body_bytes=%d client=%s",
            event_type,
            len(body),
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=400, detail="WorkOS event ID is required.")
    if store.record_workos_event(event_id, event_type, payload):
        get_task_dispatcher().dispatch("workos.process", process_workos_event, event_id)
        logger.info("workos webhook accepted event_id=%s event_type=%s queued=true", event_id, event_type)
    else:
        logger.info("workos webhook accepted event_id=%s event_type=%s duplicate=true", event_id, event_type)
    return {"accepted": True}
