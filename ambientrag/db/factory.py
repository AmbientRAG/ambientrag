"""Factory for selecting the correct database backend based on state."""
from __future__ import annotations

from pathlib import Path

from ambientrag.db.base import DatabaseBackend


def get_backend(state: dict) -> DatabaseBackend:
    """Return an appropriate backend for the current tier.

    T0 -> SQLiteBackend (file in vault/_system/ambientrag.db)
    T1+ -> PostgresBackend (psycopg2)
    """
    tier = state.get("tier", 0)

    if tier == 0:
        from ambientrag.db.sqlite_backend import SQLiteBackend

        vault_path = state.get("vault_path", ".")
        db_path = state.get("db_path")
        if not db_path:
            db_path = str(Path(vault_path) / "_system" / "ambientrag.db")
        return SQLiteBackend(db_path)
    else:
        from ambientrag.db.pg_backend import PostgresBackend

        db_url = state.get("db_url", "postgresql://localhost/ambientrag")
        return PostgresBackend(db_url)
