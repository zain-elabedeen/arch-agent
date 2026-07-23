"""Private Cloud Tasks handlers invoked with Cloud Run OIDC authentication."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel, Field

from agent.app.api.contracts import TaskRunResponse
from agent.app.config import get_settings
from agent.app.product.analysis import process_analysis_run
from agent.app.product.collector_ingest import process_collector_snapshot
from agent.app.product.knowledge import process_document
from agent.app.product.workos_sync import process_workos_event

router = APIRouter(prefix="/internal/tasks", tags=["Tasks"])


class TaskPayload(BaseModel):
    task_name: str = Field(description="Task name. Must match the path parameter.")
    args: list[Any] = Field(default_factory=list, description="Positional arguments passed to the task handler.")
    kwargs: dict[str, Any] = Field(default_factory=dict, description="Keyword arguments passed to the task handler.")


def _verify_cloud_tasks_oidc(request: Request) -> None:
    settings = get_settings()
    if settings.environment != "prod":
        return
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Cloud Tasks OIDC token is required.")
    try:
        claims = id_token.verify_oauth2_token(
            authorization.removeprefix("Bearer "),
            google_requests.Request(),
            audience=settings.gcp_tasks_oidc_audience or settings.gcp_tasks_target_url,
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Cloud Tasks OIDC token.") from None
    if settings.gcp_tasks_service_account and claims.get("email") != settings.gcp_tasks_service_account:
        raise HTTPException(status_code=403, detail="Unexpected Cloud Tasks service account.")


@router.post(
    "/{task_name}",
    response_model=TaskRunResponse,
    summary="Run Internal Task",
    description="Private Cloud Tasks handler. Production requests must present a valid Cloud Run OIDC bearer token.",
)
def run_task(task_name: str, payload: TaskPayload, request: Request) -> dict[str, bool]:
    _verify_cloud_tasks_oidc(request)
    if payload.task_name != task_name:
        raise HTTPException(status_code=400, detail="Task path and payload do not match.")
    handlers = {
        "knowledge.process": process_document,
        "workos.process": process_workos_event,
        "analysis.process": process_analysis_run,
        "collector.process": process_collector_snapshot,
    }
    handler = handlers.get(task_name)
    if not handler:
        raise HTTPException(status_code=404, detail="Unknown task.")
    handler(*payload.args, **payload.kwargs)
    return {"processed": True}
