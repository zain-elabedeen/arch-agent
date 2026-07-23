"""SQLAlchemy schema for the local product control plane.

Alembic owns production migrations. Development creates the same metadata in a
local SQLite database so the product can run without provisioning cloud infra.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, MetaData, String, Table, Text, func

metadata = MetaData()


def updated_at_column() -> Column:
    return Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


organizations = Table(
    "organizations",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("slug", String, nullable=False, unique=True),
    Column("status", String, nullable=False, default="active"),
    Column("workos_organization_id", String, unique=True),
    Column("workos_updated_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    updated_at_column(),
)

users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),
    Column("email", String, nullable=False, unique=True),
    Column("name", String, nullable=False),
    Column("is_internal", Boolean, nullable=False, default=False),
    Column("status", String, nullable=False, default="active"),
    Column("workos_user_id", String, unique=True),
    Column("workos_updated_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    updated_at_column(),
)

organization_memberships = Table(
    "organization_memberships",
    metadata,
    Column("organization_id", ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role", String, nullable=False),
    Column("status", String, nullable=False, default="active"),
    Column("workos_membership_id", String, unique=True),
    Column("workos_updated_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    updated_at_column(),
)

organization_invitations = Table(
    "organization_invitations",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
    Column("email", String, nullable=False),
    Column("role", String, nullable=False),
    Column("status", String, nullable=False),
    Column("workos_invitation_id", String, unique=True),
    Column("workos_updated_at", DateTime(timezone=True)),
    Column("invited_by_user_id", ForeignKey("users.id")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    updated_at_column(),
)

clusters = Table(
    "clusters",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
    Column("name", String, nullable=False),
    Column("environment", String, nullable=False),
    Column("connection_mode", String, nullable=False, default="helm"),
    Column("collector_status", String, nullable=False, default="pending"),
    Column("last_heartbeat_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    updated_at_column(),
)

cluster_namespaces = Table(
    "cluster_namespaces",
    metadata,
    Column("cluster_id", ForeignKey("clusters.id", ondelete="CASCADE"), primary_key=True),
    Column("namespace", String, primary_key=True),
    Column("monitored", Boolean, nullable=False, default=True),
    Column("is_system", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    updated_at_column(),
)

collector_credentials = Table(
    "collector_credentials",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
    Column("cluster_id", ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False),
    Column("token_hash", String, nullable=False),
    Column("purpose", String, nullable=False, default="collector"),
    Column("revoked", Boolean, nullable=False, default=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    updated_at_column(),
)

cluster_heartbeats = Table(
    "cluster_heartbeats",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
    Column("cluster_id", ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False),
    Column("payload", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
    updated_at_column(),
)

snapshot_runs = Table(
    "snapshot_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
    Column("cluster_id", ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False),
    Column("source_run_id", String),
    Column("snapshot", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
    updated_at_column(),
)

workload_snapshots = Table(
    "workload_snapshots",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
    Column("cluster_id", ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False),
    Column("run_id", ForeignKey("snapshot_runs.id", ondelete="CASCADE"), nullable=False),
    Column("namespace", String, nullable=False),
    Column("workload_kind", String, nullable=False),
    Column("workload_name", String, nullable=False),
    Column("payload", JSON, nullable=False, default=dict),
    updated_at_column(),
)

topology_edges = Table(
    "topology_edges",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
    Column("cluster_id", ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False),
    Column("run_id", ForeignKey("snapshot_runs.id", ondelete="CASCADE"), nullable=False),
    Column("source", String, nullable=False),
    Column("target", String, nullable=False),
    Column("payload", JSON, nullable=False, default=dict),
    updated_at_column(),
)

analysis_runs = Table(
    "analysis_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("organization_id", ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
    Column("cluster_id", ForeignKey("clusters.id", ondelete="CASCADE")),
    Column("status", String, nullable=False),
    Column("input_payload", JSON, nullable=False, default=dict),
    Column("result_payload", JSON),
    Column("knowledge_chunk_ids", JSON, nullable=False, default=list),
    Column("created_by_user_id", ForeignKey("users.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    updated_at_column(),
)

knowledge_documents = Table(
    "knowledge_documents",
    metadata,
    Column("id", String, primary_key=True),
    Column("logical_document_id", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("scope", String, nullable=False),
    Column("organization_id", ForeignKey("organizations.id", ondelete="CASCADE")),
    Column("title", String, nullable=False),
    Column("filename", String, nullable=False),
    Column("mime_type", String, nullable=False),
    Column("byte_size", Integer, nullable=False, default=0),
    Column("checksum", String),
    Column("object_key", String, nullable=False),
    Column("status", String, nullable=False),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("uploaded_by_user_id", ForeignKey("users.id"), nullable=False),
    Column("published_by_user_id", ForeignKey("users.id")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True)),
    Column("deleted_at", DateTime(timezone=True)),
    updated_at_column(),
)

knowledge_chunks = Table(
    "knowledge_chunks",
    metadata,
    Column("id", String, primary_key=True),
    Column("document_id", ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False),
    Column("scope", String, nullable=False),
    Column("organization_id", ForeignKey("organizations.id", ondelete="CASCADE")),
    Column("chunk_index", Integer, nullable=False),
    Column("content", Text, nullable=False),
    Column("content_hash", String, nullable=False),
    Column("metadata", JSON, nullable=False, default=dict),
    updated_at_column(),
)

knowledge_ingestion_jobs = Table(
    "knowledge_ingestion_jobs",
    metadata,
    Column("id", String, primary_key=True),
    Column("document_id", ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False),
    Column("status", String, nullable=False),
    Column("error_code", String),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    updated_at_column(),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", String, primary_key=True),
    Column("actor_user_id", ForeignKey("users.id"), nullable=False),
    Column("actor_type", String, nullable=False),
    Column("organization_id", ForeignKey("organizations.id", ondelete="CASCADE")),
    Column("action", String, nullable=False),
    Column("target_type", String, nullable=False),
    Column("target_id", String, nullable=False),
    Column("metadata", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
    updated_at_column(),
)

workos_event_receipts = Table(
    "workos_event_receipts",
    metadata,
    Column("id", String, primary_key=True),
    Column("event_type", String, nullable=False),
    Column("payload", JSON, nullable=False, default=dict),
    Column("processed_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    updated_at_column(),
)

workos_sync_cursors = Table(
    "workos_sync_cursors",
    metadata,
    Column("id", String, primary_key=True),
    Column("cursor", String),
    Column("created_at", DateTime(timezone=True), nullable=False),
    updated_at_column(),
)
