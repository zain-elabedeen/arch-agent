"""Outbound-only HTTP client used by the in-cluster hosted collector."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from agent.app.config import Settings
from agent.app.logging_utils import get_logger

logger = get_logger("agent.connectors.hosted_client")


class HostedCollectorClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = settings.collector_ingest_endpoint.rstrip("/")
        self.credential_path = Path(settings.collector_credential_file)
        self.retry_queue_path = Path(settings.collector_retry_queue_file)

    def _credential(self) -> str:
        if not self.credential_path.exists():
            raise RuntimeError("Collector credential has not been registered.")
        return self.credential_path.read_text(encoding="utf-8").strip()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._credential()}"}

    def _post(self, path: str, **kwargs: Any) -> requests.Response:
        attempts = max(1, int(self.settings.collector_request_attempts))
        for attempt in range(attempts):
            try:
                response = requests.post(f"{self.base_url}{path}", **kwargs)
                if response.status_code >= 500:
                    raise requests.RequestException(f"Collector endpoint returned {response.status_code}.")
                response.raise_for_status()
                return response
            except requests.RequestException:
                if attempt + 1 >= attempts:
                    raise
                time.sleep(max(0.0, self.settings.collector_retry_initial_sec) * (2**attempt))
        raise RuntimeError("Collector request retry loop exited unexpectedly.")

    def register_if_needed(self) -> None:
        if self.credential_path.exists() and self._credential():
            return
        if not self.settings.collector_registration_token:
            raise RuntimeError("ARCHAGENT_COLLECTOR_REGISTRATION_TOKEN is required for first registration.")
        response = self._post(
            "/collector/v1/register",
            json={"registration_token": self.settings.collector_registration_token},
            timeout=20,
        )
        response.raise_for_status()
        self._write_credential(str(response.json()["credential"]))

    def heartbeat(self, payload: dict[str, Any]) -> None:
        self._post("/collector/v1/heartbeat", json=payload, headers=self._headers(), timeout=20)

    def upload_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._post(
            "/collector/v1/snapshots",
            json={"snapshot": snapshot},
            headers=self._headers(),
            timeout=60,
        )

    def rotate(self) -> None:
        response = self._post("/collector/v1/credentials/rotate", headers=self._headers(), timeout=20)
        self._write_credential(str(response.json()["credential"]))

    def enqueue_snapshot(self, snapshot: dict[str, Any]) -> None:
        pending = self._read_retry_queue()
        pending.append(snapshot)
        limit = max(1, int(self.settings.collector_retry_queue_size))
        if len(pending) > limit:
            logger.warning("collector retry queue full; dropping oldest snapshot")
            pending = pending[-limit:]
        self._write_retry_queue(pending)

    def flush_snapshots(self) -> None:
        pending = self._read_retry_queue()
        while pending:
            self.upload_snapshot(pending[0])
            pending = pending[1:]
            self._write_retry_queue(pending)

    def _read_retry_queue(self) -> list[dict[str, Any]]:
        if not self.retry_queue_path.exists():
            return []
        payload = json.loads(self.retry_queue_path.read_text(encoding="utf-8"))
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    def _write_retry_queue(self, pending: list[dict[str, Any]]) -> None:
        self.retry_queue_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.retry_queue_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(pending, separators=(",", ":")), encoding="utf-8")
        temporary.replace(self.retry_queue_path)
        self.retry_queue_path.chmod(0o600)

    def _write_credential(self, credential: str) -> None:
        self.credential_path.parent.mkdir(parents=True, exist_ok=True)
        self.credential_path.write_text(credential, encoding="utf-8")
        self.credential_path.chmod(0o600)
