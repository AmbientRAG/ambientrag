"""AmbientRAG database backend abstraction."""
from __future__ import annotations

from ambientrag.db.base import DatabaseBackend
from ambientrag.db.factory import get_backend

__all__ = ["DatabaseBackend", "get_backend"]
