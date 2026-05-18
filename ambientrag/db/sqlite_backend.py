"""SQLite + sqlite-vec backend for AmbientRAG T0."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ambientrag.db.base import DatabaseBackend

# Column definitions for vault_chunks (SQLite types).
# Order matters — it defines the canonical column list.
_COLUMNS: list[tuple[str, str, str | None]] = [
    # (name, sqlite_type, default_expr_or_None)
    ("id",                   "INTEGER PRIMARY KEY AUTOINCREMENT", None),
    ("source_path",          "TEXT NOT NULL",                     None),
    ("folder",               "TEXT NOT NULL",                     None),
    ("agent_scope",          "TEXT NOT NULL",                     "'all'"),
    ("project",              "TEXT",                              None),
    ("chunk_heading",        "TEXT",                              None),
    ("chunk_text",           "TEXT NOT NULL",                     None),
    ("chunk_index",          "INTEGER NOT NULL",                  None),
    ("tags",                 "TEXT",                              None),  # JSON array
    ("doc_type",             "TEXT",                              None),
    ("status",               "TEXT NOT NULL",                     "'active'"),
    ("content_hash",         "TEXT NOT NULL",                     None),
    ("created_at",           "TEXT",                              "CURRENT_TIMESTAMP"),
    ("updated_at",           "TEXT",                              "CURRENT_TIMESTAMP"),
    ("embedding_model",      "TEXT",                              "'microsoft/harrier-oss-v1-0.6b'"),
    ("embedding_dimension",  "INTEGER",                           "1024"),
    ("embedded_at",          "TEXT",                              "CURRENT_TIMESTAMP"),
    ("enriched_summary",     "TEXT",                              None),
    ("hypothetical_questions", "TEXT",                            None),  # JSON array
    ("enriched_entities",    "TEXT",                              None),  # JSON array
    ("enrichment_model",     "TEXT",                              None),
    ("enriched_at",          "TEXT",                              None),
    ("enrichment_score",     "REAL",                              "0.0"),
    ("enrichment_source",    "TEXT",                              None),
    ("enrichment_reviewed",  "TEXT",                              None),
]

_EMBEDDING_DIM = 1024


class SQLiteBackend(DatabaseBackend):
    """T0 backend: SQLite file + sqlite-vec for vector search."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    # ── connection lifecycle ──────────────────────────────────────────

    def connect(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        # Load sqlite-vec extension for vector search.
        # macOS system Python may not support enable_load_extension
        # (compiled without SQLITE_ENABLE_LOAD_EXTENSION). In that case,
        # basic SQLite still works for schema/metadata — just no vector search.
        self._vec_available = False
        try:
            self._conn.enable_load_extension(True)
            import sqlite_vec
            sqlite_vec.load(self._conn)
            self._vec_available = True
        except (AttributeError, Exception):
            # AttributeError = enable_load_extension missing (system Python)
            # Other exceptions = sqlite-vec load failed
            pass

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.connect()
        return self._conn

    # ── schema management ────────────────────────────────────────────

    def create_schema(self) -> None:
        c = self.conn

        # Build CREATE TABLE statement
        col_defs: list[str] = []
        for name, col_type, default in _COLUMNS:
            part = f"    {name} {col_type}"
            if default is not None and "PRIMARY KEY" not in col_type:
                part += f" DEFAULT {default}"
            col_defs.append(part)

        create_sql = "CREATE TABLE IF NOT EXISTS vault_chunks (\n"
        create_sql += ",\n".join(col_defs)
        create_sql += "\n)"
        c.execute(create_sql)

        # sqlite-vec virtual table for embeddings (requires extension support)
        if self._vec_available:
            c.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vault_chunks_vec "
                f"USING vec0(embedding float[{_EMBEDDING_DIM}])"
            )

        # FTS5 for keyword search (built into SQLite, no extension needed)
        c.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS vault_chunks_fts "
            "USING fts5(chunk_text, content=vault_chunks, content_rowid=id)"
        )

        # Indexes
        for idx_name, idx_col in [
            ("idx_chunks_source", "source_path"),
            ("idx_chunks_agent_scope", "agent_scope"),
            ("idx_chunks_status", "status"),
            ("idx_chunks_folder", "folder"),
            ("idx_chunks_hash", "content_hash"),
            ("idx_chunks_enriched", "enriched_at"),
            ("idx_chunks_warmness", "enrichment_score"),
        ]:
            c.execute(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON vault_chunks({idx_col})"
            )

        c.commit()

    def verify(self) -> Tuple[bool, str]:
        try:
            if not self.table_exists("vault_chunks"):
                return False, "vault_chunks table not found"
            if self._vec_available and self.table_exists("vault_chunks_vec"):
                return True, "Vector search ready (vault_chunks + sqlite-vec)"
            elif self.table_exists("vault_chunks"):
                return True, "SQLite ready (vault_chunks + FTS5, no vector search — system Python lacks extension support)"
            return False, "Schema incomplete"
        except Exception as e:
            return False, f"Verify failed: {e}"

    def drop_schema(self) -> None:
        c = self.conn
        c.execute("DROP TABLE IF EXISTS vault_chunks_fts")
        c.execute("DROP TABLE IF EXISTS vault_chunks_vec")
        c.execute("DROP TABLE IF EXISTS vault_chunks")
        c.commit()

    # ── introspection ────────────────────────────────────────────────

    def table_exists(self, name: str) -> bool:
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow') AND name = ?",
            (name,),
        ).fetchall()
        if rows:
            return True
        # Also check for virtual tables (they show up differently in some builds)
        rows = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = ?", (name,)
        ).fetchall()
        return len(rows) > 0

    def has_column(self, table: str, col: str) -> bool:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row[1] == col for row in rows)

    # ── DML ──────────────────────────────────────────────────────────

    def execute(self, sql: str, params: Optional[tuple] = None) -> None:
        if params:
            self.conn.execute(sql, params)
        else:
            self.conn.execute(sql)
        self.conn.commit()

    def fetchall(self, sql: str, params: Optional[tuple] = None) -> list[tuple]:
        if params:
            return self.conn.execute(sql, params).fetchall()
        return self.conn.execute(sql).fetchall()

    # ── DDL helpers ──────────────────────────────────────────────────

    def add_column(self, table: str, col: str, col_type: str, default: Optional[str] = None) -> None:
        if self.has_column(table, col):
            return
        stmt = f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"
        if default is not None:
            stmt += f" DEFAULT {default}"
        self.conn.execute(stmt)
        self.conn.commit()

    def drop_column(self, table: str, col: str) -> None:
        if not self.has_column(table, col):
            return

        # SQLite 3.35+ supports ALTER TABLE DROP COLUMN
        major, minor, _ = (int(x) for x in sqlite3.sqlite_version.split("."))
        if (major, minor) >= (3, 35):
            self.conn.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
            self.conn.commit()
            return

        # Fallback: recreate-table approach for older SQLite
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        keep_cols = [r[1] for r in rows if r[1] != col]
        cols_csv = ", ".join(keep_cols)

        self.conn.execute("BEGIN")
        self.conn.execute(f"CREATE TABLE _tmp_backup AS SELECT {cols_csv} FROM {table}")
        self.conn.execute(f"DROP TABLE {table}")
        self.conn.execute(f"ALTER TABLE _tmp_backup RENAME TO {table}")
        self.conn.commit()

    # ── row helpers ──────────────────────────────────────────────────

    def count_rows(self, table: str) -> int:
        row = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return row[0] if row else 0

    # ── bulk export / import ─────────────────────────────────────────

    def export_chunks(self) -> List[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM vault_chunks")
        col_names = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            d = dict(zip(col_names, row))
            # Decode JSON-encoded list fields
            for field in ("tags", "hypothetical_questions", "enriched_entities"):
                val = d.get(field)
                if isinstance(val, str):
                    try:
                        d[field] = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        pass
            result.append(d)
        return result

    def import_chunks(self, chunks: List[Dict[str, Any]]) -> int:
        if not chunks:
            return 0

        # Determine columns from the first chunk, excluding 'id'
        cols = [k for k in chunks[0] if k != "id"]
        placeholders = ", ".join(["?"] * len(cols))
        cols_csv = ", ".join(cols)
        sql = f"INSERT INTO vault_chunks ({cols_csv}) VALUES ({placeholders})"

        count = 0
        for chunk in chunks:
            vals: list[Any] = []
            for c in cols:
                v = chunk.get(c)
                # Encode list fields as JSON
                if isinstance(v, (list, dict)):
                    v = json.dumps(v)
                vals.append(v)
            self.conn.execute(sql, tuple(vals))
            count += 1

        self.conn.commit()
        return count
