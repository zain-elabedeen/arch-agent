"""Public, credential-scoped collector ingest endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agent.app.api.contracts import (
    CollectorCredentialRotationResponse,
    CollectorHeartbeatResponse,
    CollectorRegistrationResponse,
    CollectorSnapshotResponse,
)
from agent.app.product.collector_auth import require_collector_credential
from agent.app.product.collector_ingest import ingest_snapshot, record_heartbeat
from agent.app.product.store import ProductStore, get_product_store

router = APIRouter(prefix="/collector/v1", tags=["Collector"])


class RegistrationExchange(BaseModel):
    registration_token: str = Field(description="One-time token created from /v1/clusters/{cluster_id}/registration-token.")


class Heartbeat(BaseModel):
    version: str = Field(description="Collector software version.")
    last_successful_upload_at: datetime | None = Field(None, description="Timestamp of the last successful snapshot upload.")
    permissions: list[str] = Field(default_factory=list, description="Kubernetes permissions available to the collector.")
    namespaces: list[str] = Field(default_factory=list, description="Namespaces visible to the collector.")
    modules: dict[str, Any] = Field(default_factory=dict, description="Collector module health/status metadata.")


class SnapshotUpload(BaseModel):
    snapshot: dict[str, Any] = Field(description="Canonical collector snapshot payload containing services, signals, topology, logs, and data quality.")


@router.post(
    "/register",
    response_model=CollectorRegistrationResponse,
    summary="Exchange Collector Registration Token",
    description="Exchange a one-time registration token for a scoped collector bearer credential.",
)
def register(payload: RegistrationExchange, store: ProductStore = Depends(get_product_store)) -> dict[str, Any]:
    try:
        return store.exchange_collector_registration_token(payload.registration_token)
    except LookupError:
        raise HTTPException(status_code=401, detail="Invalid or expired registration token.") from None


@router.post(
    "/heartbeat",
    response_model=CollectorHeartbeatResponse,
    summary="Record Collector Heartbeat",
    description="Record collector health and capability metadata for the credential's cluster.",
)
def heartbeat(
    payload: Heartbeat,
    credential: dict[str, Any] = Depends(require_collector_credential),
    store: ProductStore = Depends(get_product_store),
) -> dict[str, Any]:
    saved = record_heartbeat(store, credential, payload.model_dump(mode="json"))
    return {"heartbeat_id": saved["id"], "recorded": True}


@router.post(
    "/snapshots",
    response_model=CollectorSnapshotResponse,
    summary="Upload Collector Snapshot",
    description="Persist a tenant-scoped hosted snapshot uploaded by a collector credential.",
)
def snapshots(
    payload: SnapshotUpload,
    credential: dict[str, Any] = Depends(require_collector_credential),
    store: ProductStore = Depends(get_product_store),
) -> dict[str, Any]:
    result = ingest_snapshot(store, credential, payload.snapshot)
    return {
        "snapshot_run_id": result.get("id") if isinstance(result, dict) else None,
        "stored": isinstance(result, dict),
        "accepted": True,
    }


@router.post(
    "/credentials/rotate",
    response_model=CollectorCredentialRotationResponse,
    summary="Rotate Collector Credential",
    description="Revoke the presented collector credential and return a replacement credential.",
)
def rotate(
    credential: dict[str, Any] = Depends(require_collector_credential),
    store: ProductStore = Depends(get_product_store),
) -> dict[str, Any]:
    # FastAPI has already authenticated the exact presented credential.
    return store.rotate_authenticated_collector_credential(credential)
