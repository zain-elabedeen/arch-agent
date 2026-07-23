"""Staff-only internal administration routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from agent.app.api.contracts import (
    AccountDetailsResponse,
    AccountSummaryResponse,
    DocumentResponse,
    DocumentUploadResponse,
    UserResponse,
)
from agent.app.product.auth import Identity, require_staff
from agent.app.product.csrf import require_csrf
from agent.app.product.knowledge import process_document
from agent.app.product.storage import get_storage_backend
from agent.app.product.store import ProductStore, get_product_store, utcnow
from agent.app.product.tasks import get_task_dispatcher

router = APIRouter(prefix="/internal/v1", dependencies=[Depends(require_csrf)], tags=["Internal"])


class GlobalDocumentUploadCreate(BaseModel):
    filename: str = Field(description="Original filename. Directory components are stripped server-side.")
    title: str | None = Field(None, description="Optional display title. Defaults to the filename stem.")
    mime_type: str = Field("application/octet-stream", description="Uploaded document MIME type.")


def _global_document(store: ProductStore, document_id: str) -> dict[str, Any]:
    document = store.get_document(document_id, internal=True)
    if not document or document["scope"] != "global":
        raise HTTPException(status_code=404, detail="Global document not found.")
    return document


@router.get(
    "/accounts",
    response_model=list[AccountSummaryResponse],
    summary="List Accounts",
    description="Return staff-only account summaries with user, cluster, and document counts.",
)
def list_accounts(_: Identity = Depends(require_staff), store: ProductStore = Depends(get_product_store)) -> list[dict[str, Any]]:
    return store.list_accounts()


@router.get(
    "/accounts/{account_id}",
    response_model=AccountDetailsResponse,
    summary="Get Account Details",
    description="Return one staff-only account detail payload with members, invitations, clusters, documents, analysis runs, and audit events.",
)
def get_account(account_id: str, _: Identity = Depends(require_staff), store: ProductStore = Depends(get_product_store)) -> dict[str, Any]:
    account = store.get_account_details(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    return account


@router.get(
    "/users",
    response_model=list[UserResponse],
    summary="List Users",
    description="Return staff-only user records with organization memberships.",
)
def list_users(_: Identity = Depends(require_staff), store: ProductStore = Depends(get_product_store)) -> list[dict[str, Any]]:
    return store.list_users()


@router.get(
    "/knowledge/documents",
    response_model=list[DocumentResponse],
    summary="List Global Knowledge Documents",
    description="Return staff-only global knowledge documents.",
)
def list_global_documents(_: Identity = Depends(require_staff), store: ProductStore = Depends(get_product_store)) -> list[dict[str, Any]]:
    return store.list_documents(scope="global")


@router.post(
    "/knowledge/documents/uploads",
    response_model=DocumentUploadResponse,
    summary="Create Global Knowledge Upload",
    description="Create global document metadata and return the API-relative URL for uploading bytes.",
)
def create_global_upload(payload: GlobalDocumentUploadCreate, identity: Identity = Depends(require_staff), store: ProductStore = Depends(get_product_store)) -> dict[str, Any]:
    filename = Path(payload.filename).name
    document = store.create_document(
        scope="global",
        organization_id=None,
        actor_user_id=identity.user_id,
        title=payload.title or Path(filename).stem,
        filename=filename,
        mime_type=payload.mime_type,
    )
    return {**document, "upload_url": f"/internal/v1/knowledge/documents/{document['id']}/content"}


@router.put(
    "/knowledge/documents/{document_id}/content",
    status_code=204,
    summary="Upload Global Knowledge Content",
    description="Upload raw bytes for a global knowledge document.",
)
async def upload_global_content(document_id: str, request: Request, _: Identity = Depends(require_staff), store: ProductStore = Depends(get_product_store)) -> Response:
    document = _global_document(store, document_id)
    get_storage_backend().put_bytes(document["object_key"], await request.body())
    return Response(status_code=204)


@router.post(
    "/knowledge/documents/{document_id}/complete",
    response_model=DocumentResponse,
    summary="Complete Global Knowledge Upload",
    description="Mark a global document ready for asynchronous scanning, chunking, and indexing.",
)
def complete_global_upload(document_id: str, _: Identity = Depends(require_staff), store: ProductStore = Depends(get_product_store)) -> dict[str, Any]:
    _global_document(store, document_id)
    get_task_dispatcher().dispatch("knowledge.process", process_document, document_id)
    return store.get_document(document_id, internal=True) or {}


@router.post(
    "/knowledge/documents/{document_id}/publish",
    response_model=DocumentResponse,
    summary="Publish Global Knowledge Document",
    description="Publish an indexed draft or archived global document for retrieval across organizations.",
)
def publish_global_document(document_id: str, identity: Identity = Depends(require_staff), store: ProductStore = Depends(get_product_store)) -> dict[str, Any]:
    document = _global_document(store, document_id)
    if document["status"] not in {"draft", "archived"}:
        raise HTTPException(status_code=409, detail="Only indexed drafts or archived versions can be published.")
    return store.update_document(document_id, internal=True, status="published", enabled=True, published_by_user_id=identity.user_id, published_at=utcnow()) or {}


@router.post(
    "/knowledge/documents/{document_id}/archive",
    response_model=DocumentResponse,
    summary="Archive Global Knowledge Document",
    description="Archive and disable a published global knowledge document.",
)
def archive_global_document(document_id: str, _: Identity = Depends(require_staff), store: ProductStore = Depends(get_product_store)) -> dict[str, Any]:
    _global_document(store, document_id)
    return store.update_document(document_id, internal=True, status="archived", enabled=False) or {}


@router.post(
    "/knowledge/documents/{document_id}/rollback",
    response_model=DocumentResponse,
    summary="Rollback Global Knowledge Document",
    description="Republish a previous global document version.",
)
def rollback_global_document(document_id: str, identity: Identity = Depends(require_staff), store: ProductStore = Depends(get_product_store)) -> dict[str, Any]:
    return publish_global_document(document_id, identity, store)
