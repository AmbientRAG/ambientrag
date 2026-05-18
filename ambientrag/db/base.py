"""Abstract base class for AmbientRAG database backends."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple


class DatabaseBackend(ABC):
    """Interface that all AmbientRAG storage backends must implement."""

    @abstractmethod
    def connect(self) -> None:
        """Open a connection to the database."""

    @abstractmethod
    def close(self) -> None:
        """Close the database connection."""

    @abstractmethod
    def create_schema(self) -> None:
        """Create the vault_chunks table and supporting objects."""

    @abstractmethod
    def verify(self) -> Tuple[bool, str]:
        """Check that the schema is healthy. Returns (ok, message)."""

    @abstractmethod
    def drop_schema(self) -> None:
        """Drop the vault_chunks table and related objects."""

    @abstractmethod
    def table_exists(self, name: str) -> bool:
        """Return True if *name* exists as a table (or virtual table)."""

    @abstractmethod
    def execute(self, sql: str, params: Optional[tuple] = None) -> None:
        """Execute a single SQL statement."""

    @abstractmethod
    def fetchall(self, sql: str, params: Optional[tuple] = None) -> List[tuple]:
        """Execute SQL and return all rows."""

    @abstractmethod
    def add_column(self, table: str, col: str, col_type: str, default: Optional[str] = None) -> None:
        """Add a column to *table* if it does not already exist."""

    @abstractmethod
    def drop_column(self, table: str, col: str) -> None:
        """Remove a column from *table* if it exists."""

    @abstractmethod
    def has_column(self, table: str, col: str) -> bool:
        """Return True if *table* has a column named *col*."""

    @abstractmethod
    def count_rows(self, table: str) -> int:
        """Return the number of rows in *table*."""

    @abstractmethod
    def export_chunks(self) -> List[Dict[str, Any]]:
        """Export all vault_chunks rows as a list of dicts."""

    @abstractmethod
    def import_chunks(self, chunks: List[Dict[str, Any]]) -> int:
        """Insert *chunks* into vault_chunks. Returns the count inserted."""

    # Convenience ─────────────────────────────────────────────────────
    def __enter__(self) -> "DatabaseBackend":
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
