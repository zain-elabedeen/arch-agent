"""Inline development tasks and OIDC-authenticated Cloud Tasks dispatch."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, Protocol

from agent.app.config import get_settings


class TaskDispatcher(Protocol):
    def dispatch(self, task_name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any: ...


class InlineTaskDispatcher:
    def dispatch(self, task_name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)


class CloudTasksDispatcher:
    def __init__(
        self,
        *,
        project: str | None,
        queue: str | None,
        region: str,
        target_url: str | None,
        service_account: str | None,
        oidc_audience: str | None,
    ):
        missing = [
            name
            for name, value in {
                "ARCHAGENT_GCP_PROJECT_ID": project,
                "ARCHAGENT_GCP_TASKS_QUEUE": queue,
                "ARCHAGENT_GCP_TASKS_TARGET_URL": target_url,
                "ARCHAGENT_GCP_TASKS_SERVICE_ACCOUNT": service_account,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"Cloud Tasks requires: {', '.join(missing)}.")
        self.project = str(project)
        self.queue = str(queue)
        self.region = region
        self.target_url = str(target_url).rstrip("/")
        self.service_account = str(service_account)
        self.oidc_audience = str(oidc_audience or target_url)

    def dispatch(self, task_name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
        try:
            from google.cloud import tasks_v2
        except ImportError as exc:
            raise RuntimeError("Cloud Tasks dispatch requires google-cloud-tasks.") from exc
        payload = json.dumps({"task_name": task_name, "args": args, "kwargs": kwargs}, default=str).encode("utf-8")
        client = tasks_v2.CloudTasksClient()
        task_id = hashlib.sha256(payload).hexdigest()[:32]
        task = tasks_v2.Task(
            name=client.task_path(self.project, self.region, self.queue, task_id),
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=f"{self.target_url}/internal/tasks/{task_name}",
                headers={"Content-Type": "application/json"},
                body=payload,
                oidc_token=tasks_v2.OidcToken(
                    service_account_email=self.service_account,
                    audience=self.oidc_audience,
                ),
            ),
        )
        try:
            client.create_task(parent=client.queue_path(self.project, self.region, self.queue), task=task)
        except Exception as exc:
            if type(exc).__name__ != "AlreadyExists":
                raise
        return task.name


def get_task_dispatcher() -> TaskDispatcher:
    settings = get_settings()
    if settings.task_dispatcher == "cloud_tasks":
        return CloudTasksDispatcher(
            project=settings.gcp_project_id,
            queue=settings.gcp_tasks_queue,
            region=settings.gcp_region,
            target_url=settings.gcp_tasks_target_url,
            service_account=settings.gcp_tasks_service_account,
            oidc_audience=settings.gcp_tasks_oidc_audience,
        )
    return InlineTaskDispatcher()
