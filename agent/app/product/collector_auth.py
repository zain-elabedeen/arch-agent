"""Collector bearer-token authentication isolated from browser auth."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from agent.app.product.store import ProductStore, get_product_store


def require_collector_credential(
    request: Request,
    store: ProductStore = Depends(get_product_store),
) -> dict:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Collector credential is required.")
    try:
        return store.authenticate_collector(authorization.removeprefix("Bearer ").strip())
    except LookupError:
        raise HTTPException(status_code=401, detail="Invalid collector credential.") from None
