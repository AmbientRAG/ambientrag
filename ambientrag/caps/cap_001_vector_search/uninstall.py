"""CAP-001 Vector Search — uninstaller.

WARNING: This is DESTRUCTIVE — drops the vault_chunks table and all indexed data.
Requires --force flag via CLI.
"""
from __future__ import annotations

from ambientrag.utils import print_error, print_info, print_success, print_warning


def uninstall(state: dict) -> bool:
    from ambientrag.db import get_backend

    tier = state.get("tier", 1)

    print_warning("Dropping vault_chunks table — ALL indexed data will be lost!")

    if tier == 0:
        backend = get_backend(state)
        try:
            backend.connect()
            backend.drop_schema()
            print_success("vault_chunks table and related objects dropped (SQLite)")
            return True
        except Exception as e:
            print_error(f"Uninstall failed: {e}")
            return False
        finally:
            backend.close()
    else:
        from ambientrag.utils import run_sql

        db_url = state.get("db_url")
        if not db_url:
            print_error("No db_url in state")
            return False

        stmts = [
            "DROP TRIGGER IF EXISTS vault_chunks_tsv_trigger ON vault_chunks",
            "DROP TRIGGER IF EXISTS vault_chunks_updated_at ON vault_chunks",
            "DROP FUNCTION IF EXISTS vault_chunks_tsv_update()",
            "DROP FUNCTION IF EXISTS set_updated_at()",
            "DROP TABLE IF EXISTS vault_chunks CASCADE",
        ]

        for stmt in stmts:
            try:
                run_sql(db_url, stmt)
            except Exception as e:
                print_error(f"Uninstall SQL failed: {e}")
                return False

        print_success("vault_chunks table and related objects dropped")
        print_info("Note: pgvector extension preserved (shared resource)")
        return True
