"""Capability registry — loads manifest.json and resolves dependencies."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import ambientrag.state as _state

_MANIFEST_PATH = Path(__file__).parent / "manifest.json"
_manifest: dict | None = None


def _load_manifest() -> dict:
    global _manifest
    if _manifest is None:
        with _MANIFEST_PATH.open() as f:
            _manifest = json.load(f)
    return _manifest


def normalize_cap_id(cap_id: str) -> str:
    """Normalize '1', '01', '001' -> '001'."""
    return cap_id.strip().zfill(3)


def get_all_caps() -> dict:
    return _load_manifest()["capabilities"]


def get_cap(cap_id: str) -> dict | None:
    cap_id = normalize_cap_id(cap_id)
    return get_all_caps().get(cap_id)


def check_dependencies(cap_id: str, installed: dict) -> list[str]:
    """Return list of dep cap IDs that are not yet installed."""
    cap = get_cap(cap_id)
    if cap is None:
        return []
    return [dep for dep in cap.get("requires", []) if dep not in installed]


def check_tier_requirement(cap_id: str, current_tier: int) -> bool:
    """Return True if current_tier meets the cap's minimum tier."""
    cap = get_cap(cap_id)
    if cap is None:
        return False
    return current_tier >= cap.get("tier_min", 0)


def get_install_order(cap_ids: list[str], installed: dict) -> list[str]:
    """
    Return an ordered list of cap IDs to install, including uninstalled deps,
    in dependency-safe order (deps before dependents).
    """
    normalized = [normalize_cap_id(c) for c in cap_ids]
    all_caps = get_all_caps()
    order: list[str] = []
    visited: set[str] = set()

    def visit(cap_id: str) -> None:
        if cap_id in visited:
            return
        visited.add(cap_id)
        cap = all_caps.get(cap_id)
        if cap is None:
            return
        for dep in cap.get("requires", []):
            if dep not in installed:
                visit(dep)
        if cap_id not in installed:
            order.append(cap_id)

    for cid in normalized:
        visit(cid)
    return order


def get_cap_module(cap_id: str):
    """Import and return the cap module (e.g. ambientrag.caps.cap_001_vector_search)."""
    cap_id = normalize_cap_id(cap_id)
    module_name = _module_map().get(cap_id)
    if module_name is None:
        raise ImportError(f"No module found for cap {cap_id}")
    return importlib.import_module(module_name)


def _module_map() -> dict[str, str]:
    return {
        "001": "ambientrag.caps.cap_001_vector_search",
        "002": "ambientrag.caps.cap_002_enrichment",
        "003": "ambientrag.caps.cap_003_tiered_retrieval",
        "004": "ambientrag.caps.cap_004_temporal_scoring",
        "005": "ambientrag.caps.cap_005_reranker",
    }


def get_reverse_dependencies(cap_id: str, installed_caps: dict) -> dict[str, list[str]]:
    """
    Return caps that depend on cap_id.
    Returns {"hard": [...], "soft": [...]} where:
      hard = caps whose "requires" includes cap_id
      soft = caps whose "enhances" includes cap_id
    Only considers installed caps.
    """
    cap_id = normalize_cap_id(cap_id)
    all_caps = get_all_caps()
    hard: list[str] = []
    soft: list[str] = []

    for cid, info in all_caps.items():
        if cid not in installed_caps:
            continue
        if cap_id in info.get("requires", []):
            hard.append(cid)
        if cap_id in info.get("enhances", []):
            soft.append(cid)

    return {"hard": sorted(hard), "soft": sorted(soft)}


def is_cap_active(cap_id: str) -> bool:
    """Check if a CAP is installed AND enabled (convenience wrapper)."""
    return _state.is_cap_active(normalize_cap_id(cap_id))


def get_uninstall_order(cap_ids: list[str], installed: dict) -> list[str]:
    """
    Return an ordered list of cap IDs to uninstall — dependents first,
    then the targets. Topological sort for removal (reverse of install order).
    """
    normalized = [normalize_cap_id(c) for c in cap_ids]
    all_caps = get_all_caps()
    order: list[str] = []
    visited: set[str] = set()

    def visit(cap_id: str) -> None:
        if cap_id in visited:
            return
        visited.add(cap_id)
        # Find installed caps that hard-depend on this one
        for cid, info in all_caps.items():
            if cid in installed and cap_id in info.get("requires", []):
                visit(cid)
        order.append(cap_id)

    for cid in normalized:
        visit(cid)
    return order
