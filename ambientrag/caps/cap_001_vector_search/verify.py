"""CAP-001 Vector Search — verifier."""
from __future__ import annotations


def verify(state: dict) -> tuple[bool, str]:
    from ambientrag.db import get_backend

    tier = state.get("tier", 1)

    if tier == 0:
        backend = get_backend(state)
        try:
            backend.connect()
            return backend.verify()
        except Exception as e:
            return False, f"SQLite verify failed: {e}"
        finally:
            backend.close()
    else:
        db_url = state.get("db_url")
        if not db_url:
            return False, "No db_url in state"

        try:
            import psycopg2

            conn = psycopg2.connect(db_url)
            conn.autocommit = True
            try:
                with conn.cursor() as cur:
                    # Check vault_chunks table
                    cur.execute(
                        """
                        SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_name = 'vault_chunks'
                        """
                    )
                    if cur.fetchone()[0] == 0:
                        return False, "vault_chunks table not found"

                    # Check vector extension
                    cur.execute(
                        "SELECT COUNT(*) FROM pg_extension WHERE extname = 'vector'"
                    )
                    if cur.fetchone()[0] == 0:
                        return False, "pgvector extension not installed"
            finally:
                conn.close()
        except Exception as e:
            return False, f"DB connection failed: {e}"

        return True, "Vector search ready (vault_chunks + pgvector)"
