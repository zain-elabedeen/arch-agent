"""Health-check API routes."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict:
    """Minimal liveness probe for orchestrators and load balancers."""
    return {"ok": True}

