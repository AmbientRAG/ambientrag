"""PostgreSQL + pgvector backend for AmbientRAG T1+."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ambientrag.db.base import DatabaseBackend

_SCHEMA_FILE = Path(__file__).resolve().parent.parent / "caps" / "cap_001_vector_search" / "schema.sql"


class PostgresBackend(DatabaseBackend):
    """T1+ backend: PostgreSQL with pgvector."""

    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        self._conn: Any = None  # psycopg2 connection

    # ── connection lifecycle ──────────────────────────────────────────

    def connect(self) -> None:
        import psycopg2
        self._conn = psycopg2.connect(self.db_url)
        self._conn.autocommit = True

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> Any:
        if self._conn is None:
            self.connect()
        return self._conn

    # ── schema management ────────────────────────────────────────────

    def create_schema(self) -> None:
        sql = _SCHEMA_FILE.read_text()
        with self.conn.cursor() as cur:
            cur.execute(sql)

    def verify(self) -> Tuple[bool, str]:
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_name = 'vault_chunks'"
                )
                if cur.fetchone()[0] == 0:
                    return False, "vault_chunks table not found"

                cur.execute(
                    "SELECT COUNT(*) FROM pg_extension WHERE extname = 'vector'"
                )
                if cur.fetchone()[0] == 0:
                    return False, "pgvector extension not installed"
        except Exception as e:
            return False, f"DB connection failed: {e}"

        return True, "Vector search ready (vault_chunks + pgvector)"

    def drop_schema(self) -> None:
        stmts = [
            "DROP TRIGGER IF EXISTS vault_chunks_tsv_trigger ON vault_chunks",
            "DROP TRIGGER IF EXISTS vault_chunks_updated_at ON vault_chunks",
            "DROP FUNCTION IF EXISTS vault_chunks_tsv_update()",
            "DROP FUNCTION IF EXISTS set_updated_at()",
            "DROP TABLE IF EXISTS vault_chunks CASCADE",
        ]
        with self.conn.cursor() as cur:
            for stmt in stmts:
                cur.execute(stmt)

    # ── introspection ────────────────────────────────────────────────

    def table_exists(self, name: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = %s",
                (name,),
            )
            return cur.fetchone()[0] > 0

    def has_column(self, table: str, col: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, col),
            )
            return cur.fetchone()[0] > 0

    # ── DML ──────────────────────────────────────────────────────────

    def execute(self, sql: str, params: Optional[tuple] = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)

    def fetchall(self, sql: str, params: Optional[tuple] = None) -> list[tuple]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    # ── DDL helpers ──────────────────────────────────────────────────

    def add_column(self, table: str, col: str, col_type: str, default: Optional[str] = None) -> None:
        stmt = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"
        if default is not None:
            stmt += f" DEFAULT {default}"
        self.execute(stmt)

    def drop_column(self, table: str, col: str) -> None:
        self.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}")

    # ── row helpers ──────────────────────────────────────────────────

    def count_rows(self, table: str) -> int:
        rows = self.fetchall(f"SELECT COUNT(*) FROM {table}")
        return rows[0][0] if rows else 0

    # ── bulk export / import ─────────────────────────────────────────

    def export_chunks(self) -> List[Dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM vault_chunks")
            col_names = [desc[0] for desc in cur.description]
            rows = cur.fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            d = dict(zip(col_names, row))
            # Convert Postgres-specific types to portable Python types
            for key, val in d.items():
                if hasattr(val, "isoformat"):
                    d[key] = val.isoformat()
                # pgvector returns numpy arrays or strings — normalise
                if key == "embedding_v2" and val is not None:
                    if hasattr(val, "tolist"):
                        d[key] = val.tolist()
                    elif isinstance(val, str):
                        d[key] = [float(x) for x in val.strip("[]").split(",") if x.strip()]
            result.append(d)
        return result

    def import_chunks(self, chunks: List[Dict[str, Any]]) -> int:
        if not chunks:
            return 0

        # Determine columns from the first chunk, excluding 'id' (serial)
        cols = [k for k in chunks[0] if k != "id"]
        placeholders = ", ".join(["%s"] * len(cols))
        cols_csv = ", ".join(cols)
        sql = f"INSERT INTO vault_chunks ({cols_csv}) VALUES ({placeholders})"

        count = 0
        with self.conn.cursor() as cur:
            for chunk in chunks:
                vals = [chunk.get(c) for c in cols]
                cur.execute(sql, vals)
                count += 1

        return count
