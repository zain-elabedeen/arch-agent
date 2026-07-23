"""Health-check API routes."""

from __future__ import annotations

from fastapi import APIRouter

from agent.app.api.contracts import HealthResponse, ReadinessResponse


router = APIRouter(tags=["Health"])


@router.get(
    "/healthz",
    response_model=HealthResponse,
    summary="Liveness Probe",
    description="Minimal liveness probe for orchestrators and load balancers.",
)
def healthz() -> dict:
    """Minimal liveness probe for orchestrators and load balancers."""
    return {"ok": True}


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    summary="Readiness Probe",
    description="Readiness probe used by Cloud Run and container orchestrators.",
)
def readyz() -> dict:
    """Readiness probe used by Cloud Run and container orchestrators."""
    return {"ready": True}
