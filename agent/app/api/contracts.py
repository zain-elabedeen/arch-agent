"""Pydantic response contracts for the HTTP API.

These models are intentionally close to the persistence shape returned by the
current store layer. They make FastAPI's OpenAPI output usable as the frontend
contract without requiring the UI to infer shapes from example responses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict


JsonObject = dict[str, Any]


class HealthResponse(BaseModel):
    ok: bool = Field(description="Whether the API process is alive.")


class ReadinessResponse(BaseModel):
    ready: bool = Field(description="Whether the API process is ready to serve traffic.")


class UserSummaryResponse(BaseModel):
    id: str
    email: str
    name: str


class OrganizationMembershipResponse(BaseModel):
    organization_id: str
    organization_name: str
    organization_slug: str
    user_id: str
    email: str
    user_name: str
    is_internal: bool
    role: str = Field(description="Organization role, for example owner, admin, or viewer.")
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SessionResponse(BaseModel):
    user: UserSummaryResponse
    organization_id: str | None = None
    organizations: list[OrganizationMembershipResponse] = Field(default_factory=list)
    role: str | None = None
    permissions: list[str] = Field(default_factory=list)


class OrganizationSwitchResponse(BaseModel):
    organization_id: str | None = None
    workos_organization_id: str | None = None
    role: str | None = None
    permissions: list[str] = Field(default_factory=list)


class CsrfTokenResponse(BaseModel):
    csrf_token: str = Field(description="Signed double-submit CSRF token. Send it in x-archagent-csrf on unsafe requests.")


class LogoutResponse(BaseModel):
    logout_url: str = Field(description="URL the frontend should navigate to for WorkOS session logout.")
    redirect_to: str = Field(description="Allowed frontend URL WorkOS should return to after logout.")


class SignedOutResponse(BaseModel):
    signed_out: bool


class TeamMemberResponse(BaseModel):
    id: str = Field(description="User id. This value is accepted as membership_id for member update/deactivate routes.")
    email: str
    name: str
    role: str


class InvitationResponse(BaseModel):
    id: str
    organization_id: str
    email: str
    role: str
    status: str
    workos_invitation_id: str | None = None
    invited_by_user_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TeamResponse(BaseModel):
    members: list[TeamMemberResponse] = Field(default_factory=list)
    invitations: list[InvitationResponse] = Field(default_factory=list)


class MembershipResponse(BaseModel):
    organization_id: str
    user_id: str
    role: str
    status: str = "active"
    workos_membership_id: str | None = None
    workos_updated_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ClusterResponse(BaseModel):
    id: str
    organization_id: str
    name: str
    environment: str
    connection_mode: str
    collector_status: str
    last_heartbeat_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CollectorRegistrationTokenResponse(BaseModel):
    cluster_id: str
    registration_endpoint: str
    registration_token: str = Field(description="One-time token exchanged by the collector for a rotating credential.")


class NamespaceResponse(BaseModel):
    cluster_id: str
    namespace: str
    monitored: bool
    is_system: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DocumentResponse(BaseModel):
    id: str
    logical_document_id: str
    version: int
    scope: str = Field(description="Document scope: organization or global.")
    organization_id: str | None = None
    title: str
    filename: str
    mime_type: str
    byte_size: int = 0
    checksum: str | None = None
    object_key: str
    status: str
    enabled: bool
    uploaded_by_user_id: str
    published_by_user_id: str | None = None
    created_at: datetime | None = None
    published_at: datetime | None = None
    deleted_at: datetime | None = None
    updated_at: datetime | None = None


class DocumentUploadResponse(DocumentResponse):
    upload_url: str = Field(description="API-relative URL where the frontend uploads the document bytes with PUT.")


class AuditEventResponse(BaseModel):
    id: str
    actor_user_id: str
    actor_type: str
    organization_id: str | None = None
    action: str
    target_type: str
    target_id: str
    metadata: JsonObject = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AnalysisRunResponse(BaseModel):
    id: str
    organization_id: str
    cluster_id: str | None = None
    status: str
    input_payload: JsonObject = Field(default_factory=dict)
    result_payload: JsonObject | None = None
    knowledge_chunk_ids: list[str] = Field(default_factory=list)
    created_by_user_id: str
    created_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime | None = None


class AccountSummaryResponse(BaseModel):
    id: str
    name: str
    slug: str
    status: str = "active"
    workos_organization_id: str | None = None
    workos_updated_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    users: int
    clusters: int
    documents: int


class ClusterDetailResponse(ClusterResponse):
    namespaces: list[NamespaceResponse] = Field(default_factory=list)


class AccountDetailsResponse(AccountSummaryResponse):
    members: list[OrganizationMembershipResponse] = Field(default_factory=list)
    invitations: list[InvitationResponse] = Field(default_factory=list)
    clusters_detail: list[ClusterDetailResponse] = Field(default_factory=list)
    documents_detail: list[DocumentResponse] = Field(default_factory=list)
    analysis_runs: list[AnalysisRunResponse] = Field(default_factory=list)
    audit_events: list[AuditEventResponse] = Field(default_factory=list)


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    is_internal: bool
    status: str = "active"
    workos_user_id: str | None = None
    workos_updated_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    memberships: list[OrganizationMembershipResponse] = Field(default_factory=list)


class CollectorRegistrationResponse(BaseModel):
    credential: str
    expires_at: datetime
    organization_id: str
    cluster_id: str


class CollectorHeartbeatResponse(BaseModel):
    heartbeat_id: str
    recorded: bool


class CollectorSnapshotResponse(BaseModel):
    snapshot_run_id: str | None = None
    stored: bool
    accepted: bool


class CollectorCredentialRotationResponse(BaseModel):
    credential: str
    expires_at: datetime


class WorkOSWebhookResponse(BaseModel):
    accepted: bool


class TaskRunResponse(BaseModel):
    processed: bool


class AnyPayload(BaseModel):
    """Schema placeholder for externally defined payloads."""

    model_config = ConfigDict(extra="allow")
