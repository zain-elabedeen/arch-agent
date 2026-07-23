"""Customer-facing product control-plane routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from agent.app.config import Settings, get_settings
from agent.app.api.contracts import (
    AnalysisRunResponse,
    AuditEventResponse,
    ClusterResponse,
    CollectorRegistrationTokenResponse,
    DocumentResponse,
    DocumentUploadResponse,
    InvitationResponse,
    MembershipResponse,
    NamespaceResponse,
    OrganizationMembershipResponse,
    SessionResponse,
    TeamResponse,
)
from agent.app.product.auth import Identity, require_customer, require_roles
from agent.app.product.analysis import process_analysis_run
from agent.app.product.csrf import require_csrf
from agent.app.product.knowledge import process_document
from agent.app.product.storage import get_storage_backend
from agent.app.product.store import ProductStore, get_product_store
from agent.app.product.tasks import InlineTaskDispatcher, get_task_dispatcher
from agent.app.product.workos_client import get_field, get_workos_client

router = APIRouter(prefix="/v1", dependencies=[Depends(require_csrf)], tags=["Product"])


class InvitationCreate(BaseModel):
    email: str = Field(description="Email address to invite into the active organization.")
    role: Literal["admin", "viewer"] = Field("viewer", description="Role assigned when the invitation is accepted.")


class ClusterCreate(BaseModel):
    name: str = Field(description="Human-readable cluster name shown in the product UI.")
    environment: str = Field("development", description="Deployment environment label, for example development, staging, or production.")


class NamespaceConfig(BaseModel):
    namespace: str = Field(description="Kubernetes namespace name.")
    monitored: bool = Field(True, description="Whether this namespace is included in customer-visible analysis.")
    is_system: bool = Field(False, description="Whether the namespace is considered platform/system infrastructure.")


class NamespaceUpdate(BaseModel):
    namespaces: list[NamespaceConfig] = Field(default_factory=list)


class DocumentUploadCreate(BaseModel):
    filename: str = Field(description="Original filename. Directory components are stripped server-side.")
    title: str | None = Field(None, description="Optional display title. Defaults to the filename stem.")
    mime_type: str = Field("application/octet-stream", description="Uploaded document MIME type.")


class DocumentUpdate(BaseModel):
    enabled: bool = Field(description="Whether this document can be used by organization-scoped RAG.")


class AnalysisRunCreate(BaseModel):
    cluster_id: str | None = Field(None, description="Organization-owned cluster to analyze. Omit for organization-level runs.")
    payload: dict[str, Any] = Field(default_factory=dict, description="Opaque analysis input metadata persisted with the run.")


class MembershipUpdate(BaseModel):
    role: Literal["admin", "viewer"] = Field(description="New role for the organization member.")


def _owned_document(store: ProductStore, identity: Identity, document_id: str) -> dict[str, Any]:
    document = store.get_document(document_id, organization_id=identity.organization_id)
    if not document or document["scope"] != "organization" or document["organization_id"] != identity.organization_id:
        raise HTTPException(status_code=404, detail="Document not found.")
    return document


@router.get(
    "/session",
    response_model=SessionResponse,
    summary="Get Customer Session",
    description="Return the authenticated user, active organization, role, permissions, and selectable organizations.",
)
def get_session(
    identity: Identity = Depends(require_customer),
    store: ProductStore = Depends(get_product_store),
) -> dict[str, Any]:
    return {
        "user": {"id": identity.user_id, "email": identity.email, "name": identity.name},
        "organization_id": identity.organization_id,
        "organizations": store.list_memberships(user_id=identity.user_id),
        "role": identity.role,
        "permissions": list(identity.permissions),
    }


@router.get(
    "/organizations",
    response_model=list[OrganizationMembershipResponse],
    summary="List User Organizations",
    description="List organizations where the authenticated user has an active membership.",
)
def get_organizations(
    identity: Identity = Depends(require_customer),
    store: ProductStore = Depends(get_product_store),
) -> list[dict[str, Any]]:
    return store.list_memberships(user_id=identity.user_id)


@router.get(
    "/team",
    response_model=TeamResponse,
    summary="List Organization Team",
    description="Return members and pending/revoked invitations for the active organization.",
)
def get_team(identity: Identity = Depends(require_customer), store: ProductStore = Depends(get_product_store)) -> dict[str, Any]:
    return {"members": store.list_team(identity.organization_id or ""), "invitations": store.list_invitations(identity.organization_id or "")}


@router.post(
    "/team/invitations",
    response_model=InvitationResponse,
    summary="Invite Team Member",
    description="Create a WorkOS-backed organization invitation. Requires owner or admin role.",
)
def invite_team_member(payload: InvitationCreate, identity: Identity = Depends(require_roles("owner", "admin")), store: ProductStore = Depends(get_product_store)) -> dict[str, Any]:
    workos_invitation_id = None
    if get_settings().auth_mode == "workos":
        workos_organization_id = store.get_workos_organization_id(identity.organization_id or "")
        if not workos_organization_id:
            raise HTTPException(status_code=409, detail="Organization is not linked to WorkOS.")
        invitation = get_workos_client().send_invitation(
            email=payload.email,
            organization_id=workos_organization_id,
            role=payload.role,
            inviter_user_id=identity.workos_user_id,
        )
        workos_invitation_id = str(get_field(invitation, "id"))
    return store.create_invitation(
        identity.organization_id or "",
        identity.user_id,
        payload.email,
        payload.role,
        workos_invitation_id=workos_invitation_id,
    )


@router.delete(
    "/team/invitations/{invitation_id}",
    status_code=204,
    summary="Revoke Team Invitation",
    description="Revoke an organization invitation by id. Requires owner or admin role.",
)
def revoke_team_invitation(invitation_id: str, identity: Identity = Depends(require_roles("owner", "admin")), store: ProductStore = Depends(get_product_store)) -> Response:
    invitation = store.get_invitation(identity.organization_id or "", invitation_id)
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    if invitation.get("status") != "pending":
        return Response(status_code=204)
    if get_settings().auth_mode == "workos" and invitation.get("workos_invitation_id"):
        get_workos_client().revoke_invitation(str(invitation["workos_invitation_id"]))
    if not store.revoke_invitation(identity.organization_id or "", identity.user_id, invitation_id):
        raise HTTPException(status_code=404, detail="Invitation not found.")
    return Response(status_code=204)


@router.patch(
    "/team/members/{membership_id}",
    response_model=MembershipResponse,
    summary="Update Team Member Role",
    description="Update an organization member's role. The path id accepts either a WorkOS membership id or user id.",
)
def update_team_member(
    membership_id: str,
    payload: MembershipUpdate,
    identity: Identity = Depends(require_roles("owner", "admin")),
    store: ProductStore = Depends(get_product_store),
) -> dict[str, Any]:
    membership = store.get_membership(identity.organization_id or "", membership_id)
    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found.")
    if get_settings().auth_mode == "workos":
        if not membership.get("workos_membership_id"):
            raise HTTPException(status_code=409, detail="Membership is not linked to WorkOS.")
        get_workos_client().update_membership(str(membership["workos_membership_id"]), payload.role)
    return store.update_membership_role(identity.organization_id or "", identity.user_id, membership_id, payload.role)


@router.delete(
    "/team/members/{membership_id}",
    status_code=204,
    summary="Deactivate Team Member",
    description="Deactivate an organization membership. Requires owner or admin role.",
)
def deactivate_team_member(
    membership_id: str,
    identity: Identity = Depends(require_roles("owner", "admin")),
    store: ProductStore = Depends(get_product_store),
) -> Response:
    membership = store.get_membership(identity.organization_id or "", membership_id)
    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found.")
    if get_settings().auth_mode == "workos":
        if not membership.get("workos_membership_id"):
            raise HTTPException(status_code=409, detail="Membership is not linked to WorkOS.")
        get_workos_client().deactivate_membership(str(membership["workos_membership_id"]))
    store.deactivate_membership(identity.organization_id or "", identity.user_id, membership_id)
    return Response(status_code=204)


@router.get(
    "/clusters",
    response_model=list[ClusterResponse],
    summary="List Clusters",
    description="List Kubernetes clusters owned by the active organization.",
)
def get_clusters(identity: Identity = Depends(require_customer), store: ProductStore = Depends(get_product_store)) -> list[dict[str, Any]]:
    return store.list_clusters(identity.organization_id or "")


@router.post(
    "/clusters",
    response_model=ClusterResponse,
    summary="Create Cluster",
    description="Create a Helm-connected cluster record for the active organization. Requires owner or admin role.",
)
def add_cluster(payload: ClusterCreate, identity: Identity = Depends(require_roles("owner", "admin")), store: ProductStore = Depends(get_product_store)) -> dict[str, Any]:
    return store.create_cluster(identity.organization_id or "", identity.user_id, payload.name, payload.environment)


@router.post(
    "/clusters/{cluster_id}/registration-token",
    response_model=CollectorRegistrationTokenResponse,
    summary="Create Collector Registration Token",
    description="Rotate and return a one-time collector registration token for Helm installation. Requires owner or admin role.",
)
def create_collector_registration_token(
    cluster_id: str,
    identity: Identity = Depends(require_roles("owner", "admin")),
    store: ProductStore = Depends(get_product_store),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    try:
        token = store.create_collector_registration_token(identity.organization_id or "", identity.user_id, cluster_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Cluster not found.") from None
    return {
        "cluster_id": cluster_id,
        "registration_endpoint": settings.collector_registration_endpoint,
        "registration_token": token,
    }


@router.get(
    "/clusters/{cluster_id}/namespaces",
    response_model=list[NamespaceResponse],
    summary="List Cluster Namespaces",
    description="List namespace monitoring choices for a cluster.",
)
def get_cluster_namespaces(cluster_id: str, identity: Identity = Depends(require_customer), store: ProductStore = Depends(get_product_store)) -> list[dict[str, Any]]:
    if not store.get_cluster(identity.organization_id or "", cluster_id):
        raise HTTPException(status_code=404, detail="Cluster not found.")
    return store.list_namespaces(identity.organization_id or "", cluster_id)


@router.put(
    "/clusters/{cluster_id}/namespaces",
    response_model=list[NamespaceResponse],
    summary="Replace Cluster Namespaces",
    description="Replace namespace monitoring choices for a cluster. Requires owner or admin role.",
)
def set_cluster_namespaces(payload: NamespaceUpdate, cluster_id: str, identity: Identity = Depends(require_roles("owner", "admin")), store: ProductStore = Depends(get_product_store)) -> list[dict[str, Any]]:
    try:
        return store.replace_namespaces(identity.organization_id or "", identity.user_id, cluster_id, [item.model_dump() for item in payload.namespaces])
    except LookupError:
        raise HTTPException(status_code=404, detail="Cluster not found.") from None


@router.get(
    "/knowledge/documents",
    response_model=list[DocumentResponse],
    summary="List Knowledge Documents",
    description="List organization-scoped knowledge documents available to the active organization.",
)
def get_documents(identity: Identity = Depends(require_customer), store: ProductStore = Depends(get_product_store)) -> list[dict[str, Any]]:
    return store.list_documents(scope="organization", organization_id=identity.organization_id)


@router.post(
    "/knowledge/documents/uploads",
    response_model=DocumentUploadResponse,
    summary="Create Knowledge Document Upload",
    description="Create document metadata and return the API-relative URL for uploading bytes.",
)
def create_document_upload(payload: DocumentUploadCreate, identity: Identity = Depends(require_roles("owner", "admin")), store: ProductStore = Depends(get_product_store)) -> dict[str, Any]:
    filename = Path(payload.filename).name
    document = store.create_document(
        scope="organization",
        organization_id=identity.organization_id,
        actor_user_id=identity.user_id,
        title=payload.title or Path(filename).stem,
        filename=filename,
        mime_type=payload.mime_type,
    )
    return {**document, "upload_url": f"/v1/knowledge/documents/{document['id']}/content"}


@router.put(
    "/knowledge/documents/{document_id}/content",
    status_code=204,
    summary="Upload Knowledge Document Content",
    description="Upload raw document bytes to the URL returned by the upload creation endpoint.",
)
async def upload_document_content(document_id: str, request: Request, identity: Identity = Depends(require_roles("owner", "admin")), store: ProductStore = Depends(get_product_store)) -> Response:
    document = _owned_document(store, identity, document_id)
    get_storage_backend().put_bytes(document["object_key"], await request.body())
    return Response(status_code=204)


@router.post(
    "/knowledge/documents/{document_id}/complete",
    response_model=DocumentResponse,
    summary="Complete Knowledge Document Upload",
    description="Mark an uploaded document ready for asynchronous scanning, chunking, and indexing.",
)
def complete_document_upload(document_id: str, identity: Identity = Depends(require_roles("owner", "admin")), store: ProductStore = Depends(get_product_store)) -> dict[str, Any]:
    _owned_document(store, identity, document_id)
    get_task_dispatcher().dispatch("knowledge.process", process_document, document_id)
    return store.get_document(document_id, organization_id=identity.organization_id) or {}


@router.patch(
    "/knowledge/documents/{document_id}",
    response_model=DocumentResponse,
    summary="Update Knowledge Document",
    description="Enable or disable a knowledge document for organization-scoped retrieval.",
)
def update_document(document_id: str, payload: DocumentUpdate, identity: Identity = Depends(require_roles("owner", "admin")), store: ProductStore = Depends(get_product_store)) -> dict[str, Any]:
    _owned_document(store, identity, document_id)
    return store.update_document(document_id, organization_id=identity.organization_id, enabled=payload.enabled) or {}


@router.get(
    "/knowledge/documents/{document_id}/download",
    summary="Download Knowledge Document",
    description="Download the original uploaded document bytes.",
    responses={200: {"description": "Document bytes. Content-Type is the stored document MIME type."}},
)
def download_document(document_id: str, identity: Identity = Depends(require_roles("owner", "admin")), store: ProductStore = Depends(get_product_store)) -> Response:
    document = _owned_document(store, identity, document_id)
    filename = Path(document["filename"]).name.replace('"', "")
    return Response(
        content=get_storage_backend().read_bytes(document["object_key"]),
        media_type=document["mime_type"],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete(
    "/knowledge/documents/{document_id}",
    status_code=204,
    summary="Delete Knowledge Document",
    description="Delete document storage and soft-delete the document metadata.",
)
def delete_document(document_id: str, identity: Identity = Depends(require_roles("owner", "admin")), store: ProductStore = Depends(get_product_store)) -> Response:
    document = _owned_document(store, identity, document_id)
    get_storage_backend().delete(document["object_key"])
    store.delete_document(document_id, identity.user_id, organization_id=identity.organization_id)
    return Response(status_code=204)


@router.get(
    "/audit-log",
    response_model=list[AuditEventResponse],
    summary="List Audit Log",
    description="Return the latest 100 organization audit events for the active organization.",
)
def get_audit_log(identity: Identity = Depends(require_customer), store: ProductStore = Depends(get_product_store)) -> list[dict[str, Any]]:
    return store.list_audit(identity.organization_id or "")


@router.post(
    "/analysis-runs",
    response_model=AnalysisRunResponse,
    summary="Create Analysis Run",
    description="Queue or run an analysis for the active organization. Requires owner or admin role.",
)
def create_analysis_run(payload: AnalysisRunCreate, identity: Identity = Depends(require_roles("owner", "admin")), store: ProductStore = Depends(get_product_store)) -> dict[str, Any]:
    if payload.cluster_id and not store.get_cluster(identity.organization_id or "", payload.cluster_id):
        raise HTTPException(status_code=404, detail="Cluster not found.")
    run = store.create_analysis_run(identity.organization_id or "", identity.user_id, payload.cluster_id, payload.payload)
    dispatcher = get_task_dispatcher()
    if isinstance(dispatcher, InlineTaskDispatcher):
        process_analysis_run(identity.organization_id or "", run["id"], store=store)
    else:
        dispatcher.dispatch("analysis.process", process_analysis_run, identity.organization_id or "", run["id"])
    return store.get_analysis_run(identity.organization_id or "", run["id"]) or run


@router.get(
    "/analysis-runs",
    response_model=list[AnalysisRunResponse],
    summary="List Analysis Runs",
    description="List analysis runs for the active organization, optionally filtered by cluster_id.",
)
def list_analysis_runs(cluster_id: str | None = None, identity: Identity = Depends(require_customer), store: ProductStore = Depends(get_product_store)) -> list[dict[str, Any]]:
    if cluster_id and not store.get_cluster(identity.organization_id or "", cluster_id):
        raise HTTPException(status_code=404, detail="Cluster not found.")
    return store.list_analysis_runs(identity.organization_id or "", cluster_id)


@router.get(
    "/analysis-runs/{run_id}",
    response_model=AnalysisRunResponse,
    summary="Get Analysis Run",
    description="Return one analysis run by id for the active organization.",
)
def get_analysis_run(run_id: str, identity: Identity = Depends(require_customer), store: ProductStore = Depends(get_product_store)) -> dict[str, Any]:
    run = store.get_analysis_run(identity.organization_id or "", run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Analysis run not found.")
    return run
