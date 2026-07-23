from __future__ import annotations

import importlib
from types import SimpleNamespace


def test_rls_migration_enables_forced_tenant_policies(monkeypatch):
    migration = importlib.import_module("migrations.versions.0005_tenant_row_level_security")
    statements = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    sql = "\n".join(statements)
    for table in (*migration.TENANT_TABLES, "knowledge_documents", "knowledge_chunks"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql
        assert f"CREATE POLICY archagent_tenant_isolation ON {table}" in sql
