"""
Postgres persistence for normalized Kubernetes snapshots (runs, per-service rows,
aggregate signals, topology edges).
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import Engine, MetaData, Table, Uuid, Column, String, Float, Integer, DateTime, text, select, desc, insert
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from agent.app.state import ServiceTopology, TopologyEdge

_SCHEMA_READY: set[str] = set()

metadata = MetaData()

runs_t = Table(
    "runs",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("created_at", DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")),
    Column("snapshot", JSONB, nullable=True),
)

service_metrics_t = Table(
    "service_metrics",
    metadata,
    Column("run_id", Uuid(as_uuid=True), nullable=False),
    Column("service_name", String, nullable=False),
    Column("cpu", Float, nullable=False),
    Column("memory", Float, nullable=False),
    Column("cpu_usage_cores", Float, nullable=True),
    Column("memory_usage_bytes", Float, nullable=True),
    Column("replicas", Integer, nullable=False),
    Column("available_replicas", Integer, nullable=True),
    Column("unavailable_replicas", Integer, nullable=True),
    Column("restarts", Integer, nullable=False),
)

signals_t = Table(
    "signals",
    metadata,
    Column("run_id", Uuid(as_uuid=True), primary_key=True),
    Column("cpu_utilization", Float, nullable=True),
    Column("memory_utilization", Float, nullable=True),
    Column("queue_backlog", Float, nullable=True),
    Column("payload", JSONB, nullable=True),
)

topology_t = Table(
    "topology",
    metadata,
    Column("run_id", Uuid(as_uuid=True), nullable=False),
    Column("source", String, nullable=False),
    Column("target", String, nullable=False),
    Column("type", String, nullable=False),
)


def _ddl_statements() -> List[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS runs (
            id UUID PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            snapshot JSONB
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS service_metrics (
            run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            service_name TEXT NOT NULL,
            cpu DOUBLE PRECISION NOT NULL,
            memory DOUBLE PRECISION NOT NULL,
            cpu_usage_cores DOUBLE PRECISION,
            memory_usage_bytes DOUBLE PRECISION,
            replicas INTEGER NOT NULL,
            available_replicas INTEGER,
            unavailable_replicas INTEGER,
            restarts INTEGER NOT NULL,
            PRIMARY KEY (run_id, service_name)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS signals (
            run_id UUID PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
            cpu_utilization DOUBLE PRECISION,
            memory_utilization DOUBLE PRECISION,
            queue_backlog DOUBLE PRECISION,
            payload JSONB
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS topology (
            run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            type TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs (created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_topology_run_id ON topology (run_id)",
        "ALTER TABLE runs ADD COLUMN IF NOT EXISTS snapshot JSONB",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS payload JSONB",
        "ALTER TABLE service_metrics ADD COLUMN IF NOT EXISTS cpu_usage_cores DOUBLE PRECISION",
        "ALTER TABLE service_metrics ADD COLUMN IF NOT EXISTS memory_usage_bytes DOUBLE PRECISION",
        "ALTER TABLE service_metrics ADD COLUMN IF NOT EXISTS available_replicas INTEGER",
        "ALTER TABLE service_metrics ADD COLUMN IF NOT EXISTS unavailable_replicas INTEGER",
    ]


def ensure_connector_schema(engine: Engine) -> None:
    """Idempotent DDL for connector tables (PostgreSQL)."""
    if engine.dialect.name != "postgresql":
        raise RuntimeError(f"Connector schema requires PostgreSQL; got dialect={engine.dialect.name}")
    key = str(engine.url)
    if key in _SCHEMA_READY:
        return
    with engine.begin() as conn:
        for stmt in _ddl_statements():
            conn.execute(text(stmt))
    _SCHEMA_READY.add(key)


def save_run(conn: Connection, data: Dict[str, Any]) -> uuid.UUID:
    """
    Insert one run and child rows. ``data`` matches ``normalize`` output plus
    optional ``topology`` override (same as normalize output).
    """
    run_id = uuid.uuid4()
    conn.execute(insert(runs_t).values(id=run_id, snapshot=data))

    services: List[Dict[str, Any]] = data.get("services") or []
    for svc in services:
        conn.execute(
            insert(service_metrics_t).values(
                run_id=run_id,
                service_name=svc["name"],
                cpu=float(svc.get("cpu") or 0.0),
                memory=float(svc.get("memory") or 0.0),
                cpu_usage_cores=svc.get("cpu_usage_cores"),
                memory_usage_bytes=svc.get("memory_usage_bytes"),
                replicas=int(svc.get("replicas") or 0),
                available_replicas=svc.get("available_replicas"),
                unavailable_replicas=svc.get("unavailable_replicas"),
                restarts=int(svc.get("restarts") or 0),
            )
        )

    sig = data.get("signals") or {}
    conn.execute(
        insert(signals_t).values(
            run_id=run_id,
            cpu_utilization=sig.get("cpu_utilization"),
            memory_utilization=sig.get("memory_utilization"),
            queue_backlog=sig.get("queue_backlog"),
            payload=sig,
        )
    )

    topo = data.get("topology") or {}
    for edge in topo.get("edges") or []:
        conn.execute(
            insert(topology_t).values(
                run_id=run_id,
                source=edge.get("from") or edge.get("from_service"),
                target=edge.get("to") or edge.get("to_service"),
                type=edge.get("type") or "http",
            )
        )

    return run_id


def _latest_run_id(conn: Connection) -> Optional[uuid.UUID]:
    row = conn.execute(select(runs_t.c.id).order_by(desc(runs_t.c.created_at)).limit(1)).first()
    if not row:
        return None
    return row[0]


def load_run_as_raw_state(conn: Connection, run_id: Optional[uuid.UUID]) -> Tuple[Dict[str, float], Dict[str, Any], uuid.UUID]:
    """
    Build ``raw_signals`` and ``raw_topology`` for ``GraphState`` from stored rows.

    Raises:
        LookupError: ``no_snapshot`` (no rows), ``run_not_found`` (explicit id absent),
            ``missing_signals`` (corrupt/incomplete row).
    """
    rid = run_id
    if rid is None:
        rid = _latest_run_id(conn)
        if rid is None:
            raise LookupError("no_snapshot")
    else:
        rid = run_id
        exists = conn.execute(select(runs_t.c.id).where(runs_t.c.id == rid)).first()
        if not exists:
            raise LookupError("run_not_found")

    sig_row = conn.execute(
        select(
            signals_t.c.cpu_utilization,
            signals_t.c.memory_utilization,
            signals_t.c.queue_backlog,
            signals_t.c.payload,
        ).where(signals_t.c.run_id == rid)
    ).mappings().first()
    if not sig_row:
        raise LookupError("missing_signals")

    raw_signals: Dict[str, float] = {}
    payload = sig_row.get("payload") or {}
    if isinstance(payload, dict):
        for key, val in payload.items():
            if val is None:
                continue
            if isinstance(val, (int, float, bool)):
                raw_signals[key] = float(val)
    if sig_row["cpu_utilization"] is not None:
        raw_signals["cpu_utilization"] = float(sig_row["cpu_utilization"])
    if sig_row["memory_utilization"] is not None:
        raw_signals["memory_utilization"] = float(sig_row["memory_utilization"])
    if sig_row["queue_backlog"] is not None:
        raw_signals["queue_backlog"] = float(sig_row["queue_backlog"])

    svc_rows = conn.execute(
        select(service_metrics_t).where(service_metrics_t.c.run_id == rid).order_by(service_metrics_t.c.service_name)
    ).mappings().all()

    services_list = [r["service_name"] for r in svc_rows]
    total_restarts = sum(int(r["restarts"] or 0) for r in svc_rows)
    if total_restarts:
        raw_signals["pod_restart_total"] = float(total_restarts)

    top_rows = conn.execute(select(topology_t).where(topology_t.c.run_id == rid)).mappings().all()
    edges = [{"from": r["source"], "to": r["target"], "type": r["type"]} for r in top_rows]

    svc_set = set(services_list)
    for e in edges:
        svc_set.add(e["from"])
        svc_set.add(e["to"])
    topo_services = sorted(svc_set)

    raw_topology = ServiceTopology(
        services=topo_services,
        edges=[TopologyEdge.model_validate(e) for e in edges],
    ).model_dump(by_alias=True)

    return raw_signals, raw_topology, rid
