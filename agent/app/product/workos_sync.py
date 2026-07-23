"""WorkOS webhook processing entrypoints."""

from __future__ import annotations

from typing import Any

from agent.app.config import get_settings
from agent.app.product.store import ProductStore, get_product_store
from agent.app.product.workos_client import WorkOSClientAdapter, get_field, get_workos_client


def process_workos_event(event_id: str, *, store: ProductStore | None = None) -> None:
    """Apply a durable lifecycle event before marking it processed."""
    (store or get_product_store()).apply_workos_event(event_id)


def _event_payload(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return event
    if hasattr(event, "model_dump"):
        return event.model_dump(mode="json")
    if hasattr(event, "dict"):
        return event.dict()
    return {"id": get_field(event, "id"), "event": get_field(event, "event", "type")}


def reconcile_workos_events(
    *,
    store: ProductStore | None = None,
    client: WorkOSClientAdapter | None = None,
    max_pages: int = 10,
) -> None:
    store = store or get_product_store()
    client = client or get_workos_client()
    cursor = store.get_workos_sync_cursor()
    for _ in range(max_pages):
        response = client.list_events(after=cursor)
        events = get_field(response, "list", "data", default=[]) or []
        for event in events:
            payload = _event_payload(event)
            event_id = str(get_field(event, "id", default=payload.get("id") or ""))
            event_type = str(get_field(event, "event", "type", default=payload.get("event") or "unknown"))
            if event_id and store.record_workos_event(event_id, event_type, payload):
                process_workos_event(event_id, store=store)
        metadata = get_field(response, "list_metadata", "listMetadata", default={})
        next_cursor = get_field(metadata, "after")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = str(next_cursor)
        store.set_workos_sync_cursor(cursor)
        if not events:
            break


def main() -> None:
    """Repair webhook processing after transient task-delivery failures."""
    store = get_product_store()
    for event_id in store.list_pending_workos_event_ids():
        process_workos_event(event_id, store=store)
    if get_settings().workos_api_key and get_settings().workos_client_id:
        reconcile_workos_events(store=store)


if __name__ == "__main__":
    main()
