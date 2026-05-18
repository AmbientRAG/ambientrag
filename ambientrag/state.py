"""State management for AmbientRAG — persists at ~/.ambientrag/state.json."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

def _get_state_dir() -> Path:
    """State dir — overridable via AMBIENTRAG_STATE_DIR for testing."""
    override = os.environ.get("AMBIENTRAG_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".ambientrag"


# These are module-level for backward compat, but _get_state_dir() is
# the source of truth. Tests override via env var.
STATE_DIR = _get_state_dir()
STATE_FILE = STATE_DIR / "state.json"

_DEFAULT_STATE = {
    "vault_path": None,
    "tier": 1,
    "db_url": None,
    "installed_caps": {},
}


def load_state() -> dict:
    state_file = _get_state_dir() / "state.json"
    if not state_file.exists():
        return dict(_DEFAULT_STATE)
    with state_file.open() as f:
        data = json.load(f)
    # Fill missing keys with defaults
    for k, v in _DEFAULT_STATE.items():
        data.setdefault(k, v)
    return data


def save_state(state: dict) -> None:
    state_dir = _get_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "state.json").open("w") as f:
        json.dump(state, f, indent=2)


def is_initialized() -> bool:
    state = load_state()
    return state.get("vault_path") is not None


def get_installed_caps() -> dict:
    return load_state().get("installed_caps", {})


def mark_cap_installed(cap_id: str, version: str) -> None:
    state = load_state()
    state.setdefault("installed_caps", {})[cap_id] = {
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "version": version,
    }
    save_state(state)


def get_tier() -> int:
    return load_state().get("tier", 1)


def set_tier(tier: int) -> None:
    state = load_state()
    state["tier"] = tier
    save_state(state)


def get_vault_path() -> Path | None:
    vp = load_state().get("vault_path")
    return Path(vp) if vp else None


def get_db_url() -> str | None:
    return load_state().get("db_url")


def get_db_path() -> str | None:
    """Return the SQLite database path (T0 only), or None."""
    return load_state().get("db_path")


def mark_cap_disabled(cap_id: str) -> None:
    """Mark a CAP as disabled (schema preserved, skipped at runtime)."""
    state = load_state()
    caps = state.get("installed_caps", {})
    if cap_id in caps:
        caps[cap_id]["enabled"] = False
        save_state(state)


def mark_cap_enabled(cap_id: str) -> None:
    """Re-enable a disabled CAP."""
    state = load_state()
    caps = state.get("installed_caps", {})
    if cap_id in caps:
        caps[cap_id].pop("enabled", None)  # absence = enabled (backward compat)
        save_state(state)


def is_cap_active(cap_id: str) -> bool:
    """Check if a CAP is both installed AND enabled."""
    state = load_state()
    caps = state.get("installed_caps", {})
    if cap_id not in caps:
        return False
    return caps[cap_id].get("enabled", True)


def mark_cap_uninstalled(cap_id: str) -> None:
    """Remove a CAP from installed_caps in state."""
    state = load_state()
    caps = state.get("installed_caps", {})
    caps.pop(cap_id, None)
    save_state(state)


def mark_platform_connected(platform: str, companion_path: str) -> None:
    """Record that a platform has been connected with a companion doc."""
    state = load_state()
    state.setdefault("connected_platforms", {})[platform] = {
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "companion_path": companion_path,
    }
    save_state(state)


def get_connected_platforms() -> dict:
    """Return dict of connected platforms and their metadata."""
    return load_state().get("connected_platforms", {})


# ---------------------------------------------------------------------------
# Tool state management
# ---------------------------------------------------------------------------

def get_installed_tools() -> dict:
    return load_state().get("installed_tools", {})


def mark_tool_installed(tool_id: str, version: str) -> None:
    state = load_state()
    state.setdefault("installed_tools", {})[tool_id] = {
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "version": version,
    }
    save_state(state)


def mark_tool_uninstalled(tool_id: str) -> None:
    state = load_state()
    state.get("installed_tools", {}).pop(tool_id, None)
    save_state(state)


def mark_tool_disabled(tool_id: str) -> None:
    """Mark a tool as disabled (schema preserved, skipped at runtime)."""
    state = load_state()
    tools = state.get("installed_tools", {})
    if tool_id in tools:
        tools[tool_id]["enabled"] = False
        save_state(state)


def mark_tool_enabled(tool_id: str) -> None:
    """Re-enable a disabled tool."""
    state = load_state()
    tools = state.get("installed_tools", {})
    if tool_id in tools:
        tools[tool_id].pop("enabled", None)  # absence = enabled (backward compat)
        save_state(state)


def is_tool_active(tool_id: str) -> bool:
    """Check if a tool is both installed AND enabled."""
    state = load_state()
    tools = state.get("installed_tools", {})
    if tool_id not in tools:
        return False
    return tools[tool_id].get("enabled", True)
