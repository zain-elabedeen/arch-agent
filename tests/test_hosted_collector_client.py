from __future__ import annotations

import json

import requests

from agent.app.config import Settings
from agent.app.connectors.hosted_client import HostedCollectorClient


class Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._payload


def _client(tmp_path, **values):
    settings = Settings(
        environment="test",
        collector_credential_file=str(tmp_path / "credential"),
        collector_retry_queue_file=str(tmp_path / "retry.json"),
        collector_retry_initial_sec=0,
        **values,
    )
    client = HostedCollectorClient(settings)
    client.credential_path.write_text("collector-secret", encoding="utf-8")
    return client


def test_collector_http_calls_retry_transient_server_errors(monkeypatch, tmp_path):
    client = _client(tmp_path, collector_request_attempts=3)
    responses = iter([Response(503), Response(200)])
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(args[0])
        return next(responses)

    monkeypatch.setattr("agent.app.connectors.hosted_client.requests.post", fake_post)

    client.heartbeat({"version": "test"})

    assert len(calls) == 2


def test_collector_retry_queue_is_disk_backed_and_bounded(monkeypatch, tmp_path):
    client = _client(tmp_path, collector_retry_queue_size=2)
    client.enqueue_snapshot({"sequence": 1})
    client.enqueue_snapshot({"sequence": 2})
    client.enqueue_snapshot({"sequence": 3})
    uploaded = []
    monkeypatch.setattr(client, "upload_snapshot", uploaded.append)

    client.flush_snapshots()

    assert uploaded == [{"sequence": 2}, {"sequence": 3}]
    assert json.loads(client.retry_queue_path.read_text(encoding="utf-8")) == []
