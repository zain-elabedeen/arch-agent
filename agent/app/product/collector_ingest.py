"""Hosted collector persistence helpers."""

from __future__ import annotations

from typing import Any

from agent.app.product.store import ProductStore
from agent.app.product.store import get_product_store
from agent.app.product.tasks import InlineTaskDispatcher, get_task_dispatcher


def record_heartbeat(store: ProductStore, credential: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return store.record_collector_heartbeat(credential, payload)


def process_collector_snapshot(
    organization_id: str,
    cluster_id: str,
    snapshot: dict[str, Any],
    *,
    store: ProductStore | None = None,
) -> dict[str, Any]:
    return (store or get_product_store()).store_collector_snapshot(
        {"organization_id": organization_id, "cluster_id": cluster_id},
        snapshot,
    )


def ingest_snapshot(store: ProductStore, credential: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any] | str:
    dispatcher = get_task_dispatcher()
    if isinstance(dispatcher, InlineTaskDispatcher):
        return process_collector_snapshot(
            credential["organization_id"],
            credential["cluster_id"],
            snapshot,
            store=store,
        )
    return dispatcher.dispatch(
        "collector.process",
        process_collector_snapshot,
        credential["organization_id"],
        credential["cluster_id"],
        snapshot,
    )
