"""CAP-001 Vector Search — installer."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ambientrag.utils import (
    check_db_exists,
    create_db,
    print_error,
    print_info,
    print_success,
    print_warning,
    run_sql_file,
)

_SCHEMA = Path(__file__).parent / "schema.sql"

_VAULT_DIRS = [
    "_system/indexer",
    "_system/models",
    "_system/caveman-cache",
    "_system/logs",
    "agents/claude-code/inbox",
    "projects",
    "references",
    "policies",
]

# Common locations where the Harrier model might already be downloaded.
# The installer checks all of these so users can drop models into the zip
# folder, their Downloads, or anywhere else obvious. The first match wins.
_MODEL_SEARCH_PATHS = [
    # Relative to vault
    "_system/models/harrier-0.6b",
    # Relative to home
    "~/.cache/huggingface/hub/models--microsoft--harrier-oss-v1-0.6b",
    "~/Documents/ambientrag/_system/models/harrier-0.6b",
    "~/.ambientrag/models/harrier-0.6b",
    # Relative to ambientrag source/install dir (the zip care package)
    "models/harrier-0.6b",
    "harrier-0.6b",
    # Common download locations
    "~/Downloads/harrier-0.6b",
    "~/Desktop/harrier-0.6b",
]

_MODEL_MARKER = "model.safetensors"  # file that confirms model is present


def _find_existing_model(vault_path: Path) -> Path | None:
    """Search common locations for an already-downloaded Harrier model."""
    target = vault_path / "_system" / "models" / "harrier-0.6b"

    # Already in the right place?
    if (target / _MODEL_MARKER).exists():
        return target

    for search_path in _MODEL_SEARCH_PATHS:
        candidate = Path(search_path).expanduser()
        if not candidate.is_absolute():
            # Try relative to vault
            candidate = vault_path / search_path
        if (candidate / _MODEL_MARKER).exists():
            return candidate

    return None


def _link_model(source: Path, vault_path: Path) -> bool:
    """Symlink or copy an existing model into the vault's model directory."""
    target = vault_path / "_system" / "models" / "harrier-0.6b"
    if source.resolve() == target.resolve():
        return True  # already in the right place

    target.parent.mkdir(parents=True, exist_ok=True)

    # Try symlink first (saves 1.1GB of disk)
    try:
        if target.exists() or target.is_symlink():
            if target.is_symlink():
                target.unlink()
            else:
                shutil.rmtree(target)
        target.symlink_to(source.resolve())
        return True
    except OSError:
        # Symlink failed (cross-device, permissions) — copy instead
        try:
            shutil.copytree(source, target, dirs_exist_ok=True)
            return True
        except Exception:
            return False


def install(state: dict) -> bool:
    tier = state.get("tier", 1)
    db_url = state.get("db_url")
    vault_path = Path(state["vault_path"])

    if tier == 0:
        # T0: SQLite backend — no Postgres needed
        from ambientrag.db import get_backend

        print_info("T0 mode: using SQLite + sqlite-vec (no PostgreSQL needed)")
        backend = get_backend(state)
        try:
            backend.connect()
            backend.create_schema()
            print_success("SQLite schema created (vault_chunks + sqlite-vec)")
        except Exception as e:
            print_error(f"SQLite schema creation failed: {e}")
            return False
        finally:
            backend.close()
    else:
        # T1+: PostgreSQL backend
        from ambientrag.utils import check_command
        has_postgres = check_command("psql")

        if not has_postgres:
            print_warning("PostgreSQL not found on this machine.")
            print_info("  Install with: brew install postgresql@17")
            print_info("  Then start it: brew services start postgresql@17")
            print_info("  Then re-run: ambientrag init --tier 1")
            print_info("")
            print_info("Vault directory structure will still be created.")
        else:
            # Check postgresql@17 via Homebrew (informational only)
            try:
                result = subprocess.run(
                    ["brew", "list", "postgresql@17"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    print_success("postgresql@17 is installed via Homebrew")
                else:
                    print_info("PostgreSQL found (not via Homebrew — that's fine)")
            except (FileNotFoundError, subprocess.TimeoutExpired):
                print_info("PostgreSQL found (Homebrew not available — that's fine)")

            # Create database if needed
            db_name = "ambientrag"
            if not check_db_exists(db_name):
                print_info(f"Creating database '{db_name}'...")
                if create_db(db_name):
                    print_success(f"Database '{db_name}' created")
                else:
                    print_error(f"Failed to create database '{db_name}'. Is PostgreSQL running?")
                    print_info("  Start it: brew services start postgresql@17")
                    print_info("  Then re-run: ambientrag init --tier 1")
                    return False
            else:
                print_success(f"Database '{db_name}' already exists")

            # Apply schema
            print_info("Applying vector search schema...")
            try:
                run_sql_file(db_url, _SCHEMA)
                print_success("Schema applied (vault_chunks table + indexes)")
            except Exception as e:
                print_error(f"Schema apply failed: {e}")
                return False

        if not has_postgres:
            # Still create vault dirs, but warn
            pass

    # Create vault directory structure
    print_info("Creating vault directory structure...")
    for rel_dir in _VAULT_DIRS:
        target = vault_path / rel_dir
        target.mkdir(parents=True, exist_ok=True)
    print_success("Vault directories created")

    # Check for existing Harrier model
    existing_model = _find_existing_model(vault_path)
    if existing_model:
        model_target = vault_path / "_system" / "models" / "harrier-0.6b"
        if existing_model.resolve() == model_target.resolve():
            print_success(f"Harrier model found at {model_target}")
        elif _link_model(existing_model, vault_path):
            print_success(f"Harrier model found at {existing_model}")
            print_success(f"Linked to {model_target}")
        else:
            print_warning(f"Harrier model found at {existing_model} but failed to link")
            print_info(f"  Manually copy it to: {model_target}/")

    if tier >= 1:
        from ambientrag.utils import check_command
        if not check_command("psql"):
            print_warning("CAP-001 partially installed — re-run init after installing PostgreSQL.")

    return True
