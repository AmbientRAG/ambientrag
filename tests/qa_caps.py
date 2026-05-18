#!/usr/bin/env python3
"""
QA test suite for AmbientRAG CAP installer.

Spins up isolated test environments, runs CAP install permutations,
verifies each one, then tears everything down. Uses a temporary DB
name and state dir per test so nothing touches the real install.

Usage:
    python tests/qa_caps.py              # run all tests
    python tests/qa_caps.py --keep       # don't clean up (for debugging)
    python tests/qa_caps.py -k test_005  # run a specific test by name
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# ── Config ────────────────────────────────────────────────────────────

DB_PREFIX = "ambientrag_qa_"  # test DBs: ambientrag_qa_001, etc.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── Test result tracking ──────────────────────────────────────────────

@dataclass
class TestResult:
    name: str
    passed: bool
    message: str = ""
    duration_ms: float = 0


@dataclass
class TestSuite:
    results: list[TestResult] = field(default_factory=list)

    def record(self, result: TestResult):
        self.results.append(result)
        icon = "✓" if result.passed else "✗"
        color = "\033[32m" if result.passed else "\033[31m"
        reset = "\033[0m"
        ms = f" ({result.duration_ms:.0f}ms)" if result.duration_ms else ""
        print(f"  {color}{icon}{reset} {result.name}{ms}")
        if not result.passed and result.message:
            for line in result.message.strip().splitlines():
                print(f"      {line}")

    def summary(self) -> int:
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        total = len(self.results)
        print(f"\n{'─' * 60}")
        if failed == 0:
            print(f"\033[32m  All {total} tests passed\033[0m")
        else:
            print(f"\033[31m  {failed}/{total} tests failed\033[0m")
            for r in self.results:
                if not r.passed:
                    print(f"    ✗ {r.name}: {r.message[:80]}")
        print(f"{'─' * 60}")
        return 1 if failed > 0 else 0


# ── Isolated test environment ─────────────────────────────────────────

class TestEnv:
    """An isolated test environment with its own state dir, vault dir, and DB."""

    def __init__(self, name: str, tier: int = 1):
        self.name = name
        self.tier = tier
        self.db_name = f"{DB_PREFIX}{name}"
        self.tmp_dir = Path(tempfile.mkdtemp(prefix=f"ambientrag_qa_{name}_"))
        self.vault_dir = self.tmp_dir / "vault"
        self.state_dir = self.tmp_dir / "state"
        self.db_url = f"postgresql://localhost/{self.db_name}"
        self._db_created = False

    def setup(self) -> bool:
        """Create temp dirs and DB. Returns False if Postgres isn't available (T1+)."""
        self.vault_dir.mkdir(parents=True)
        self.state_dir.mkdir(parents=True)

        # Patch state module to use our isolated dir
        state_file = self.state_dir / "state.json"
        os.environ["AMBIENTRAG_STATE_DIR"] = str(self.state_dir)

        if self.tier == 0:
            # T0: SQLite — no Postgres needed
            db_path = str(self.vault_dir / "_system" / "ambientrag.db")
            state = {
                "vault_path": str(self.vault_dir),
                "tier": 0,
                "db_url": None,
                "db_path": db_path,
                "installed_caps": {},
            }
            state_file.write_text(json.dumps(state, indent=2))
            return True

        # T1+: Create test database
        try:
            result = subprocess.run(
                ["createdb", self.db_name],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                if "already exists" in result.stderr:
                    self._db_created = True
                else:
                    return False
            else:
                self._db_created = True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

        # Write initial state
        state = {
            "vault_path": str(self.vault_dir),
            "tier": self.tier,
            "db_url": self.db_url,
            "installed_caps": {},
        }
        state_file.write_text(json.dumps(state, indent=2))

        return True

    def teardown(self, keep: bool = False):
        """Drop test DB and remove temp dirs."""
        os.environ.pop("AMBIENTRAG_STATE_DIR", None)

        if self._db_created:
            subprocess.run(
                ["dropdb", "--if-exists", self.db_name],
                capture_output=True, timeout=10,
            )

        if not keep and self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def load_state(self) -> dict:
        state_file = self.state_dir / "state.json"
        if state_file.exists():
            return json.loads(state_file.read_text())
        return {}

    def save_state(self, state: dict):
        state_file = self.state_dir / "state.json"
        state_file.write_text(json.dumps(state, indent=2))

    def get_installed_caps(self) -> set[str]:
        state = self.load_state()
        return set(state.get("installed_caps", {}).keys())

    def run_cli(self, *args: str, expect_fail: bool = False) -> subprocess.CompletedProcess:
        """Run ambientrag CLI in a subprocess with isolated state."""
        env = os.environ.copy()
        env["AMBIENTRAG_STATE_DIR"] = str(self.state_dir)

        result = subprocess.run(
            [sys.executable, "-m", "ambientrag.cli"] + list(args),
            capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_ROOT),
            env=env,
        )

        if not expect_fail and result.returncode != 0:
            raise RuntimeError(
                f"CLI failed (rc={result.returncode}):\n"
                f"STDOUT: {result.stdout[-500:]}\n"
                f"STDERR: {result.stderr[-500:]}"
            )

        return result


@contextmanager
def test_env(name: str, tier: int = 1, keep: bool = False):
    """Context manager that sets up and tears down an isolated test environment."""
    env = TestEnv(name, tier)
    if not env.setup():
        raise RuntimeError("Failed to set up test environment (is Postgres running?)")
    try:
        yield env
    finally:
        env.teardown(keep=keep)


# ── Helpers for install/verify in-process ─────────────────────────────

def install_cap(env: TestEnv, cap_id: str) -> bool:
    """Install a CAP directly via Python (faster than subprocess)."""
    import importlib

    # Patch state module paths
    import ambientrag.state as _state
    original_dir = _state.STATE_DIR
    original_file = _state.STATE_FILE
    _state.STATE_DIR = env.state_dir
    _state.STATE_FILE = env.state_dir / "state.json"

    try:
        state = env.load_state()

        # Import the cap module
        mod_name = f"ambientrag.caps.cap_{cap_id}_" + _cap_suffix(cap_id)
        mod = importlib.import_module(mod_name)
        ok = mod.install.install(state)

        if ok:
            success, msg = mod.verify.verify(state)
            if success:
                # Update state
                from datetime import datetime, timezone
                state = env.load_state()
                state.setdefault("installed_caps", {})[cap_id] = {
                    "installed_at": datetime.now(timezone.utc).isoformat(),
                    "version": "test",
                }
                env.save_state(state)
                return True
        return ok
    finally:
        _state.STATE_DIR = original_dir
        _state.STATE_FILE = original_file


def verify_cap(env: TestEnv, cap_id: str) -> tuple[bool, str]:
    """Verify a CAP directly via Python."""
    import importlib

    mod_name = f"ambientrag.caps.cap_{cap_id}_" + _cap_suffix(cap_id)
    mod = importlib.import_module(mod_name)
    state = env.load_state()
    return mod.verify.verify(state)


def uninstall_cap(env: TestEnv, cap_id: str) -> bool:
    """Uninstall a CAP directly via Python (faster than subprocess)."""
    import importlib
    import ambientrag.state as _state

    original_dir = _state.STATE_DIR
    original_file = _state.STATE_FILE
    _state.STATE_DIR = env.state_dir
    _state.STATE_FILE = env.state_dir / "state.json"

    try:
        state = env.load_state()
        mod_name = f"ambientrag.caps.cap_{cap_id}_" + _cap_suffix(cap_id)
        mod = importlib.import_module(mod_name)
        ok = mod.uninstall.uninstall(state)

        if ok:
            # Remove from state
            state = env.load_state()
            state.get("installed_caps", {}).pop(cap_id, None)
            env.save_state(state)
        return ok
    finally:
        _state.STATE_DIR = original_dir
        _state.STATE_FILE = original_file


def _cap_suffix(cap_id: str) -> str:
    """Return the module suffix for a cap ID."""
    return {
        "001": "vector_search",
        "002": "enrichment",
        "003": "tiered_retrieval",
        "004": "temporal_scoring",
        "005": "reranker",
    }[cap_id]


# ── Test cases ────────────────────────────────────────────────────────

def test_001_foundation(env: TestEnv) -> TestResult:
    """CAP-001 alone — the minimum viable install."""
    ok = install_cap(env, "001")
    if not ok:
        return TestResult("001_foundation", False, "CAP-001 install failed")

    success, msg = verify_cap(env, "001")
    if not success:
        return TestResult("001_foundation", False, f"Verify failed: {msg}")

    # Verify DB has vault_chunks table
    import psycopg2
    conn = psycopg2.connect(env.db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM vault_chunks")
            count = cur.fetchone()[0]
    finally:
        conn.close()

    return TestResult("001_foundation", True, f"vault_chunks exists, {count} rows")


def test_001_idempotent(env: TestEnv) -> TestResult:
    """CAP-001 can be installed twice without error."""
    install_cap(env, "001")
    ok = install_cap(env, "001")
    return TestResult("001_idempotent", ok, "Second install succeeded" if ok else "Second install failed")


def test_001_then_004(env: TestEnv) -> TestResult:
    """CAP-004 (Temporal Scoring) bolts onto 001, skipping 002-003."""
    ok = install_cap(env, "001")
    if not ok:
        return TestResult("001_then_004", False, "CAP-001 install failed")

    ok = install_cap(env, "004")
    if not ok:
        return TestResult("001_then_004", False, "CAP-004 install failed")

    installed = env.get_installed_caps()
    if "002" in installed or "003" in installed:
        return TestResult("001_then_004", False, f"Unexpected caps installed: {installed}")

    if installed != {"001", "004"}:
        return TestResult("001_then_004", False, f"Expected {{001, 004}}, got {installed}")

    # Verify temporal columns exist
    import psycopg2
    conn = psycopg2.connect(env.db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'vault_chunks' AND column_name IN ('document_kind', 'valid_from', 'valid_until')
            """)
            cols = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()

    expected_cols = {"document_kind", "valid_from", "valid_until"}
    if cols != expected_cols:
        return TestResult("001_then_004", False, f"Missing columns: {expected_cols - cols}")

    return TestResult("001_then_004", True, "001+004 installed, temporal columns verified")


def test_001_002_003_chain(env: TestEnv) -> TestResult:
    """Sequential dependency chain: 001 → 002 → 003."""
    for cap in ["001", "002", "003"]:
        ok = install_cap(env, cap)
        if not ok:
            return TestResult("001_002_003_chain", False, f"CAP-{cap} install failed")

    installed = env.get_installed_caps()
    if installed != {"001", "002", "003"}:
        return TestResult("001_002_003_chain", False, f"Expected {{001,002,003}}, got {installed}")

    return TestResult("001_002_003_chain", True, "Full chain installed")




def test_all_compatible_caps(env: TestEnv) -> TestResult:
    """Install all T1-compatible CAPs: 001, 002, 003, 004."""
    for cap in ["001", "002", "003", "004"]:
        ok = install_cap(env, cap)
        if not ok:
            return TestResult("all_compatible_caps", False, f"CAP-{cap} install failed")

    installed = env.get_installed_caps()
    expected = {"001", "002", "003", "004"}
    if installed != expected:
        return TestResult("all_compatible_caps", False, f"Expected {expected}, got {installed}")

    return TestResult("all_compatible_caps", True, "All 4 T1-compatible caps installed")


def test_004_idempotent(env: TestEnv) -> TestResult:
    """CAP-004 schema migration is safe to re-run."""
    install_cap(env, "001")
    install_cap(env, "004")
    ok = install_cap(env, "004")
    return TestResult("004_idempotent", ok, "Re-install succeeded" if ok else "Re-install failed")


def test_003_without_002_fails(env: TestEnv) -> TestResult:
    """CAP-003 requires 002 — installing without it should fail dependency check."""
    install_cap(env, "001")

    from ambientrag.caps import registry
    missing = registry.check_dependencies("003", env.load_state().get("installed_caps", {}))

    if "002" not in missing:
        return TestResult("003_without_002_fails", False, f"Expected 002 in missing deps, got {missing}")

    return TestResult("003_without_002_fails", True, "Correctly identified 002 as missing dep")


def test_002_without_001_fails(env: TestEnv) -> TestResult:
    """CAP-002 requires 001 — dependency check catches it."""
    from ambientrag.caps import registry
    missing = registry.check_dependencies("002", {})

    if "001" not in missing:
        return TestResult("002_without_001_fails", False, f"Expected 001 in missing deps, got {missing}")

    return TestResult("002_without_001_fails", True, "Correctly identified 001 as missing dep")


def test_005_tier_rejection(env: TestEnv) -> TestResult:
    """CAP-005 requires T2 — should be rejected on T1."""
    from ambientrag.caps import registry
    allowed = registry.check_tier_requirement("005", env.load_state().get("tier", 1))

    if allowed:
        return TestResult("005_tier_rejection", False, "CAP-005 was allowed on T1 — should require T2")

    return TestResult("005_tier_rejection", True, "Correctly rejected CAP-005 on T1")


def test_005_tier2_allowed(env: TestEnv) -> TestResult:
    """CAP-005 is allowed on T2."""
    from ambientrag.caps import registry
    allowed = registry.check_tier_requirement("005", 2)

    if not allowed:
        return TestResult("005_tier2_allowed", False, "CAP-005 rejected on T2 — should be allowed")

    return TestResult("005_tier2_allowed", True, "CAP-005 correctly allowed on T2")


def test_install_order_resolves_deps(env: TestEnv) -> TestResult:
    """Requesting CAP-003 auto-includes 001 and 002 in correct order."""
    from ambientrag.caps import registry
    order = registry.get_install_order(["003"], {})

    if order != ["001", "002", "003"]:
        return TestResult("install_order_resolves_deps", False, f"Expected [001,002,003], got {order}")

    return TestResult("install_order_resolves_deps", True, "Dependency resolution: [001, 002, 003]")


def test_install_order_skips_installed(env: TestEnv) -> TestResult:
    """If 001 is already installed, requesting 003 only queues 002 and 003."""
    from ambientrag.caps import registry
    order = registry.get_install_order(["003"], {"001": {"version": "test"}})

    if order != ["002", "003"]:
        return TestResult("install_order_skips_installed", False, f"Expected [002,003], got {order}")

    return TestResult("install_order_skips_installed", True, "Skipped already-installed 001")


def test_normalize_cap_ids(env: TestEnv) -> TestResult:
    """Cap ID normalization: '1', '01', '001' all resolve to '001'."""
    from ambientrag.caps import registry
    for raw, expected in [("1", "001"), ("01", "001"), ("001", "001"), ("5", "005"), ("12", "012")]:
        result = registry.normalize_cap_id(raw)
        if result != expected:
            return TestResult("normalize_cap_ids", False, f"normalize({raw!r}) = {result!r}, expected {expected!r}")

    return TestResult("normalize_cap_ids", True, "All normalizations correct")


def test_full_cycle_install_verify_status(env: TestEnv) -> TestResult:
    """Full lifecycle: init state → install 001 → install 004 → verify both."""
    ok = install_cap(env, "001")
    if not ok:
        return TestResult("full_cycle", False, "CAP-001 failed")

    ok = install_cap(env, "004")
    if not ok:
        return TestResult("full_cycle", False, "CAP-004 failed")

    # Verify both
    for cap in ["001", "004"]:
        success, msg = verify_cap(env, cap)
        if not success:
            return TestResult("full_cycle", False, f"CAP-{cap} verify failed: {msg}")

    # Check state file is coherent
    state = env.load_state()
    caps = set(state.get("installed_caps", {}).keys())
    if caps != {"001", "004"}:
        return TestResult("full_cycle", False, f"State has {caps}, expected {{001, 004}}")

    return TestResult("full_cycle", True, "Init → install → verify → state all coherent")


# ── Lifecycle tests (uninstall, disable, enable) ─────────────────────

def test_uninstall_leaf_cap(env: TestEnv) -> TestResult:
    """Install 001+004, uninstall 004, verify 004 columns gone, 001 still works."""
    ok = install_cap(env, "001")
    if not ok:
        return TestResult("uninstall_leaf_cap", False, "CAP-001 install failed")
    ok = install_cap(env, "004")
    if not ok:
        return TestResult("uninstall_leaf_cap", False, "CAP-004 install failed")

    ok = uninstall_cap(env, "004")
    if not ok:
        return TestResult("uninstall_leaf_cap", False, "CAP-004 uninstall failed")

    # Verify 004 not in installed
    if "004" in env.get_installed_caps():
        return TestResult("uninstall_leaf_cap", False, "004 still in installed_caps")

    # Verify temporal columns are gone
    import psycopg2
    conn = psycopg2.connect(env.db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'vault_chunks'
                  AND column_name IN ('document_kind', 'valid_from', 'valid_until')
            """)
            cols = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()

    if cols:
        return TestResult("uninstall_leaf_cap", False, f"Temporal columns still exist: {cols}")

    # Verify 001 still works
    success, msg = verify_cap(env, "001")
    if not success:
        return TestResult("uninstall_leaf_cap", False, f"CAP-001 verify failed after 004 uninstall: {msg}")

    return TestResult("uninstall_leaf_cap", True, "Uninstalled 004, 001 still OK, temporal cols gone")


def test_uninstall_idempotent(env: TestEnv) -> TestResult:
    """Uninstall something not installed -> graceful error (returns non-zero or no crash)."""
    ok = install_cap(env, "001")
    if not ok:
        return TestResult("uninstall_idempotent", False, "CAP-001 install failed")

    # 004 is not installed — uninstall should exit non-zero or print error
    result = env.run_cli("cap", "uninstall", "004", expect_fail=True)
    # As long as it doesn't crash with a traceback, it's graceful
    if "Traceback" in result.stderr:
        return TestResult("uninstall_idempotent", False,
                          f"Got traceback: {result.stderr[-300:]}")

    return TestResult("uninstall_idempotent", True, "Graceful error for uninstalling non-installed cap")


def test_uninstall_with_hard_dep(env: TestEnv) -> TestResult:
    """Install 001+002+003, try uninstall 002 -> blocked (003 depends on it)."""
    for cap in ["001", "002", "003"]:
        ok = install_cap(env, cap)
        if not ok:
            return TestResult("uninstall_with_hard_dep", False, f"CAP-{cap} install failed")

    # Check in-process: get_reverse_dependencies should show 003 as hard dep of 002
    from ambientrag.caps import registry
    installed = env.load_state().get("installed_caps", {})
    rev = registry.get_reverse_dependencies("002", installed)

    if "003" not in rev["hard"]:
        return TestResult("uninstall_with_hard_dep", False,
                          f"003 not in hard deps of 002: {rev}")

    # Try CLI — should exit non-zero and NOT remove 002
    result = env.run_cli("cap", "uninstall", "002", expect_fail=True)

    # Verify 002 is still installed
    if "002" not in env.get_installed_caps():
        return TestResult("uninstall_with_hard_dep", False, "002 was removed despite hard dep")

    return TestResult("uninstall_with_hard_dep", True, "Uninstall blocked by hard dependent 003")


def test_uninstall_cascade(env: TestEnv) -> TestResult:
    """Install 001+002+003, uninstall 002 with cascade -> removes 003 then 002."""
    for cap in ["001", "002", "003"]:
        ok = install_cap(env, cap)
        if not ok:
            return TestResult("uninstall_cascade", False, f"CAP-{cap} install failed")

    # Verify uninstall order in-process
    from ambientrag.caps import registry
    installed = env.load_state().get("installed_caps", {})
    order = registry.get_uninstall_order(["002"], installed)

    # Should be 003 then 002
    if order != ["003", "002"]:
        return TestResult("uninstall_cascade", False, f"Expected [003, 002], got {order}")

    # Actually uninstall in order (in-process — CLI would require interactive confirm)
    for cid in order:
        ok = uninstall_cap(env, cid)
        if not ok:
            return TestResult("uninstall_cascade", False, f"Uninstall of {cid} failed")

    remaining = env.get_installed_caps()
    if remaining != {"001"}:
        return TestResult("uninstall_cascade", False, f"Expected only 001, got {remaining}")

    return TestResult("uninstall_cascade", True, "Cascade removed 003 then 002, 001 remains")


def test_disable_enable_cycle(env: TestEnv) -> TestResult:
    """Install 001+004, disable 004, verify inactive, enable, verify active."""
    ok = install_cap(env, "001")
    if not ok:
        return TestResult("disable_enable_cycle", False, "CAP-001 install failed")
    ok = install_cap(env, "004")
    if not ok:
        return TestResult("disable_enable_cycle", False, "CAP-004 install failed")

    import ambientrag.state as _state
    original_dir = _state.STATE_DIR
    original_file = _state.STATE_FILE
    _state.STATE_DIR = env.state_dir
    _state.STATE_FILE = env.state_dir / "state.json"

    try:
        os.environ["AMBIENTRAG_STATE_DIR"] = str(env.state_dir)

        _state.mark_cap_disabled("004")
        if _state.is_cap_active("004"):
            return TestResult("disable_enable_cycle", False, "004 still active after disable")

        _state.mark_cap_enabled("004")
        if not _state.is_cap_active("004"):
            return TestResult("disable_enable_cycle", False, "004 not active after enable")
    finally:
        _state.STATE_DIR = original_dir
        _state.STATE_FILE = original_file
        os.environ["AMBIENTRAG_STATE_DIR"] = str(env.state_dir)

    return TestResult("disable_enable_cycle", True, "Disable/enable cycle works correctly")


def test_disable_preserves_schema(env: TestEnv) -> TestResult:
    """Install 001+004, disable 004, verify temporal columns still exist."""
    ok = install_cap(env, "001")
    if not ok:
        return TestResult("disable_preserves_schema", False, "CAP-001 install failed")
    ok = install_cap(env, "004")
    if not ok:
        return TestResult("disable_preserves_schema", False, "CAP-004 install failed")

    import ambientrag.state as _state
    original_dir = _state.STATE_DIR
    original_file = _state.STATE_FILE
    _state.STATE_DIR = env.state_dir
    _state.STATE_FILE = env.state_dir / "state.json"

    try:
        os.environ["AMBIENTRAG_STATE_DIR"] = str(env.state_dir)
        _state.mark_cap_disabled("004")
    finally:
        _state.STATE_DIR = original_dir
        _state.STATE_FILE = original_file
        os.environ["AMBIENTRAG_STATE_DIR"] = str(env.state_dir)

    # Verify columns still present
    import psycopg2
    conn = psycopg2.connect(env.db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'vault_chunks'
                  AND column_name IN ('document_kind', 'valid_from', 'valid_until')
            """)
            cols = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()

    expected = {"document_kind", "valid_from", "valid_until"}
    if cols != expected:
        return TestResult("disable_preserves_schema", False, f"Missing cols: {expected - cols}")

    return TestResult("disable_preserves_schema", True, "Disable preserves temporal columns in DB")


def test_cannot_disable_001(env: TestEnv) -> TestResult:
    """Foundation cannot be disabled."""
    ok = install_cap(env, "001")
    if not ok:
        return TestResult("cannot_disable_001", False, "CAP-001 install failed")

    # Run disable via CLI
    env.run_cli("cap", "disable", "001", expect_fail=True)

    # Verify 001 is still enabled (the CLI should have refused)
    import ambientrag.state as _state
    original_dir = _state.STATE_DIR
    original_file = _state.STATE_FILE
    _state.STATE_DIR = env.state_dir
    _state.STATE_FILE = env.state_dir / "state.json"

    try:
        os.environ["AMBIENTRAG_STATE_DIR"] = str(env.state_dir)
        active = _state.is_cap_active("001")
    finally:
        _state.STATE_DIR = original_dir
        _state.STATE_FILE = original_file
        os.environ["AMBIENTRAG_STATE_DIR"] = str(env.state_dir)

    if not active:
        return TestResult("cannot_disable_001", False, "CAP-001 was disabled — should have been rejected")

    return TestResult("cannot_disable_001", True, "CAP-001 disable correctly rejected")


def test_reverse_deps_hard_and_soft(env: TestEnv) -> TestResult:
    """Verify hard/soft categorization of reverse deps."""
    from ambientrag.caps import registry

    # Build a scenario: 001 is required by 002, 004, 005. No enhances yet.
    installed = {"001": {}, "002": {}, "003": {}, "004": {}}
    rev = registry.get_reverse_dependencies("001", installed)

    # 002 and 004 require 001
    if "002" not in rev["hard"]:
        return TestResult("reverse_deps_hard_and_soft", False, f"002 not in hard deps: {rev}")
    if "004" not in rev["hard"]:
        return TestResult("reverse_deps_hard_and_soft", False, f"004 not in hard deps: {rev}")

    # 003 requires 002, not 001 directly
    if "003" in rev["hard"]:
        return TestResult("reverse_deps_hard_and_soft", False, f"003 should not be in hard deps of 001: {rev}")

    # No soft deps currently
    if rev["soft"]:
        return TestResult("reverse_deps_hard_and_soft", False, f"Unexpected soft deps: {rev['soft']}")

    return TestResult("reverse_deps_hard_and_soft", True, "Hard/soft categorization correct")


def test_companion_generation_001_only(env: TestEnv) -> TestResult:
    """Companion doc with only CAP-001 — no enrichment/temporal sections."""
    from ambientrag.companion import generate_companion

    state = {
        "vault_path": str(env.vault_dir),
        "installed_caps": {
            "001": {"installed_at": "2026-01-01T00:00:00Z", "version": "0.1.0"},
        },
    }
    doc = generate_companion("codex", state)

    # Must have core sections
    if "## Your MCP Connection" not in doc:
        return TestResult("companion_001_only", False, "Missing MCP Connection section")
    if "## How to Search" not in doc:
        return TestResult("companion_001_only", False, "Missing How to Search section")
    if "## Available Capabilities" not in doc:
        return TestResult("companion_001_only", False, "Missing Available Capabilities section")
    if "## Saving Notes" not in doc:
        return TestResult("companion_001_only", False, "Missing Saving Notes section")

    # Must NOT have CAP-002+ sections
    if "## Writing Notes with Enrichment" in doc:
        return TestResult("companion_001_only", False, "Enrichment section present without CAP-002")
    if "## Token-Efficient Retrieval" in doc:
        return TestResult("companion_001_only", False, "Tiered Retrieval present without CAP-003")
    if "## Document Freshness" in doc:
        return TestResult("companion_001_only", False, "Document Freshness present without CAP-004")

    # Must have generated comment
    if "<!-- Generated by ambientrag connect." not in doc:
        return TestResult("companion_001_only", False, "Missing generated comment header")

    return TestResult("companion_001_only", True, "001-only doc has core sections, no extras")


def test_companion_generation_with_002(env: TestEnv) -> TestResult:
    """Companion doc with CAP-001+002 — enrichment section present."""
    from ambientrag.companion import generate_companion

    state = {
        "vault_path": str(env.vault_dir),
        "installed_caps": {
            "001": {"installed_at": "2026-01-01T00:00:00Z", "version": "0.1.0"},
            "002": {"installed_at": "2026-01-01T00:00:00Z", "version": "0.2.0"},
        },
    }
    doc = generate_companion("claude-code", state)

    if "## Writing Notes with Enrichment" not in doc:
        return TestResult("companion_with_002", False, "Enrichment section missing with CAP-002")
    if "hyde_caveman" not in doc:
        return TestResult("companion_with_002", False, "hyde_caveman not mentioned in enrichment section")

    # Frontmatter template should include enrichment fields
    if "hyde_questions:" not in doc:
        return TestResult("companion_with_002", False, "hyde_questions missing from frontmatter template")

    # Should NOT have CAP-003+ sections
    if "## Token-Efficient Retrieval" in doc:
        return TestResult("companion_with_002", False, "Tiered Retrieval present without CAP-003")

    return TestResult("companion_with_002", True, "001+002 doc has enrichment section")


def test_companion_generation_with_all(env: TestEnv) -> TestResult:
    """Companion doc with all caps — all sections present."""
    from ambientrag.companion import generate_companion

    state = {
        "vault_path": str(env.vault_dir),
        "installed_caps": {
            "001": {"installed_at": "2026-01-01T00:00:00Z", "version": "0.1.0"},
            "002": {"installed_at": "2026-01-01T00:00:00Z", "version": "0.2.0"},
            "003": {"installed_at": "2026-01-01T00:00:00Z", "version": "0.3.0"},
            "004": {"installed_at": "2026-01-01T00:00:00Z", "version": "0.3.0"},
        },
    }
    doc = generate_companion("codex", state)

    expected_sections = [
        "## Your MCP Connection",
        "## How to Search",
        "## Writing Notes with Enrichment",
        "## Token-Efficient Retrieval",
        "## Document Freshness",
        "## Available Capabilities",
        "## Saving Notes",
    ]
    for section in expected_sections:
        if section not in doc:
            return TestResult("companion_all_caps", False, f"Missing section: {section}")

    # Frontmatter template should include enrichment + temporal fields
    if "document_kind: static" not in doc:
        return TestResult("companion_all_caps", False, "document_kind missing from frontmatter template")

    # Check line count is reasonable (under 200)
    line_count = len(doc.splitlines())
    if line_count > 200:
        return TestResult("companion_all_caps", False, f"Doc is {line_count} lines (max 200)")

    return TestResult("companion_all_caps", True, f"All sections present, {line_count} lines")


def test_cap_install_auto_refreshes_companion(env: TestEnv) -> TestResult:
    """Installing a cap auto-refreshes companion docs for connected platforms."""
    from ambientrag.companion import generate_companion, write_companion
    import ambientrag.state as _st

    # Set up state with 001 installed and codex connected
    state = env.load_state()
    state["installed_caps"] = {
        "001": {"installed_at": "2026-01-01T00:00:00Z", "version": "0.1.0"},
    }

    # Write initial companion
    vault_path = state["vault_path"]
    companions_dir = Path(vault_path) / "_system" / "companions"
    companions_dir.mkdir(parents=True, exist_ok=True)

    doc_path = companions_dir / "codex.md"
    initial_doc = generate_companion("codex", state)
    doc_path.write_text(initial_doc, encoding="utf-8")

    # Mark platform as connected
    state["connected_platforms"] = {
        "codex": {
            "connected_at": "2026-01-01T00:00:00Z",
            "companion_path": str(doc_path),
        },
    }

    # Now simulate installing CAP-002
    state["installed_caps"]["002"] = {
        "installed_at": "2026-01-02T00:00:00Z",
        "version": "0.2.0",
    }

    # Call refresh (this is what cap_install does after install)
    from ambientrag.companion import refresh_all_companions
    refreshed = refresh_all_companions(state)

    if "codex" not in refreshed:
        return TestResult("cap_install_auto_refresh", False, "codex not in refreshed list")

    # Read updated doc
    updated_doc = doc_path.read_text(encoding="utf-8")

    # Should now have enrichment section
    if "## Writing Notes with Enrichment" not in updated_doc:
        return TestResult("cap_install_auto_refresh", False,
                          "Enrichment section missing after refresh with 002")

    # Initial doc should NOT have had it
    if "## Writing Notes with Enrichment" in initial_doc:
        return TestResult("cap_install_auto_refresh", False,
                          "Initial doc already had enrichment (test is wrong)")

    return TestResult("cap_install_auto_refresh", True, "Auto-refresh added enrichment section after 002 install")


def test_enhances_field_in_manifest(env: TestEnv) -> TestResult:
    """Verify all caps have enhances field."""
    from ambientrag.caps import registry
    all_caps = registry.get_all_caps()

    for cap_id, info in all_caps.items():
        if "enhances" not in info:
            return TestResult("enhances_field_in_manifest", False,
                              f"CAP-{cap_id} missing 'enhances' field")
        if not isinstance(info["enhances"], list):
            return TestResult("enhances_field_in_manifest", False,
                              f"CAP-{cap_id} enhances is not a list")

    return TestResult("enhances_field_in_manifest", True,
                      f"All {len(all_caps)} caps have enhances field")


# ── T0 SQLite tests ─────────────────────────────────────────────────

def test_sqlite_backend_create_verify(env: TestEnv) -> TestResult:
    """SQLiteBackend: create_schema then verify -> True."""
    from ambientrag.db.sqlite_backend import SQLiteBackend

    db_path = str(env.tmp_dir / "test_backend.db")
    backend = SQLiteBackend(db_path)
    try:
        backend.connect()
        backend.create_schema()
        ok, msg = backend.verify()
        if not ok:
            return TestResult("sqlite_backend_create_verify", False, f"Verify failed: {msg}")
        return TestResult("sqlite_backend_create_verify", True, msg)
    except Exception as e:
        return TestResult("sqlite_backend_create_verify", False, f"Exception: {e}")
    finally:
        backend.close()


def test_sqlite_backend_add_drop_column(env: TestEnv) -> TestResult:
    """SQLiteBackend: add_column, has_column, drop_column round-trip."""
    from ambientrag.db.sqlite_backend import SQLiteBackend

    db_path = str(env.tmp_dir / "test_cols.db")
    backend = SQLiteBackend(db_path)
    try:
        backend.connect()
        backend.create_schema()

        # Add a test column
        backend.add_column("vault_chunks", "test_col", "TEXT", "'hello'")
        if not backend.has_column("vault_chunks", "test_col"):
            return TestResult("sqlite_backend_add_drop_column", False, "Column not found after add")

        # Idempotent add
        backend.add_column("vault_chunks", "test_col", "TEXT", "'hello'")

        # Drop it
        backend.drop_column("vault_chunks", "test_col")
        if backend.has_column("vault_chunks", "test_col"):
            return TestResult("sqlite_backend_add_drop_column", False, "Column still exists after drop")

        # Idempotent drop
        backend.drop_column("vault_chunks", "test_col")

        return TestResult("sqlite_backend_add_drop_column", True, "add/drop column round-trip OK")
    except Exception as e:
        return TestResult("sqlite_backend_add_drop_column", False, f"Exception: {e}")
    finally:
        backend.close()


def test_t0_init_no_postgres(env: TestEnv) -> TestResult:
    """T0 init: CAP-001 installs on SQLite, no Postgres needed."""
    ok = install_cap(env, "001")
    if not ok:
        return TestResult("t0_init_no_postgres", False, "CAP-001 install failed on T0")

    success, msg = verify_cap(env, "001")
    if not success:
        return TestResult("t0_init_no_postgres", False, f"Verify failed: {msg}")

    # Confirm SQLite DB was created
    state = env.load_state()
    db_path = state.get("db_path")
    if not db_path or not Path(db_path).exists():
        return TestResult("t0_init_no_postgres", False, f"SQLite DB not found at {db_path}")

    return TestResult("t0_init_no_postgres", True, f"T0 CAP-001 installed via SQLite: {msg}")


def test_t0_cap_004_on_sqlite(env: TestEnv) -> TestResult:
    """T0: install 001 + 004 on SQLite, verify temporal columns exist."""
    ok = install_cap(env, "001")
    if not ok:
        return TestResult("t0_cap_004_on_sqlite", False, "CAP-001 install failed on T0")

    ok = install_cap(env, "004")
    if not ok:
        return TestResult("t0_cap_004_on_sqlite", False, "CAP-004 install failed on T0")

    success, msg = verify_cap(env, "004")
    if not success:
        return TestResult("t0_cap_004_on_sqlite", False, f"CAP-004 verify failed: {msg}")

    # Double-check columns directly
    from ambientrag.db import get_backend
    state = env.load_state()
    backend = get_backend(state)
    try:
        backend.connect()
        for col in ("document_kind", "valid_from", "valid_until"):
            if not backend.has_column("vault_chunks", col):
                return TestResult("t0_cap_004_on_sqlite", False, f"Missing column: {col}")
    finally:
        backend.close()

    return TestResult("t0_cap_004_on_sqlite", True, "T0 001+004 installed, temporal columns verified")


def test_migration_status(env: TestEnv) -> TestResult:
    """T0: migrate status verifies SQLite backend info."""
    ok = install_cap(env, "001")
    if not ok:
        return TestResult("migration_status", False, "CAP-001 install failed on T0")

    # Verify backend type and row count in-process (same as migrate status does)
    from ambientrag.db import get_backend

    state = env.load_state()
    tier = state.get("tier", 0)

    if tier != 0:
        return TestResult("migration_status", False, f"Expected tier 0, got {tier}")

    backend = get_backend(state)
    try:
        backend.connect()
        ok, msg = backend.verify()
        if not ok:
            return TestResult("migration_status", False, f"Backend verify failed: {msg}")
        count = backend.count_rows("vault_chunks")
    except Exception as e:
        return TestResult("migration_status", False, f"Backend query failed: {e}")
    finally:
        backend.close()

    # Check that it's a SQLiteBackend
    from ambientrag.db.sqlite_backend import SQLiteBackend
    if not isinstance(backend, SQLiteBackend):
        return TestResult("migration_status", False, f"Expected SQLiteBackend, got {type(backend)}")

    return TestResult("migration_status", True,
                      f"T0 migrate status: SQLite backend, {count} rows, verify OK")


def test_packaging_smoke(env: TestEnv) -> TestResult:
    """Verify package installs cleanly and CLI works in a fresh venv."""
    import time
    t0 = time.monotonic()

    tmp_dir = Path(tempfile.mkdtemp(prefix="ambientrag_pkg_smoke_"))
    try:
        # 1. Create a fresh venv
        venv_dir = tmp_dir / "venv"
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return TestResult("packaging_smoke", False,
                              f"venv creation failed: {result.stderr[:200]}")

        # 2. Find pip and python in the new venv
        if sys.platform == "win32":
            pip = str(venv_dir / "Scripts" / "pip")
            python = str(venv_dir / "Scripts" / "python")
        else:
            pip = str(venv_dir / "bin" / "pip")
            python = str(venv_dir / "bin" / "python")

        # 3. pip install from project root
        result = subprocess.run(
            [pip, "install", str(PROJECT_ROOT)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return TestResult("packaging_smoke", False,
                              f"pip install failed: {result.stderr[:300]}")

        # 4. ambientrag --help should work
        result = subprocess.run(
            [python, "-m", "ambientrag", "--help"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return TestResult("packaging_smoke", False,
                              f"--help failed (rc={result.returncode}): {result.stderr[:200]}")

        # 5. ambientrag cap list should work and mention Vector Search
        result = subprocess.run(
            [python, "-m", "ambientrag", "cap", "list"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return TestResult("packaging_smoke", False,
                              f"cap list failed: {result.stderr[:200]}")
        if "Vector Search" not in result.stdout:
            return TestResult("packaging_smoke", False,
                              "'Vector Search' not in cap list output")

        # 6. ambientrag --version should output the version
        result = subprocess.run(
            [python, "-m", "ambientrag", "--version"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return TestResult("packaging_smoke", False,
                              f"--version failed: {result.stderr[:200]}")

        from ambientrag import __version__
        if __version__ not in result.stdout:
            return TestResult("packaging_smoke", False,
                              f"Version mismatch: expected '{__version__}' in '{result.stdout.strip()}'")

        duration = (time.monotonic() - t0) * 1000
        return TestResult("packaging_smoke", True,
                          "Package installs and CLI runs in isolated venv",
                          duration_ms=duration)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_install_reinstall_after_uninstall(env: TestEnv) -> TestResult:
    """Uninstall 004, reinstall 004, verify clean state."""
    ok = install_cap(env, "001")
    if not ok:
        return TestResult("reinstall_after_uninstall", False, "CAP-001 install failed")
    ok = install_cap(env, "004")
    if not ok:
        return TestResult("reinstall_after_uninstall", False, "CAP-004 first install failed")

    ok = uninstall_cap(env, "004")
    if not ok:
        return TestResult("reinstall_after_uninstall", False, "CAP-004 uninstall failed")

    # Reinstall
    ok = install_cap(env, "004")
    if not ok:
        return TestResult("reinstall_after_uninstall", False, "CAP-004 reinstall failed")

    # Verify it's installed and working
    if "004" not in env.get_installed_caps():
        return TestResult("reinstall_after_uninstall", False, "004 not in installed after reinstall")

    success, msg = verify_cap(env, "004")
    if not success:
        return TestResult("reinstall_after_uninstall", False, f"Verify failed after reinstall: {msg}")

    return TestResult("reinstall_after_uninstall", True, "Uninstall then reinstall works cleanly")


# ── TOOL-001 Search Metrics tests ────────────────────────────────────

def test_tool_001_install_verify(env: TestEnv) -> TestResult:
    """TOOL-001 install on T1 creates search_metrics table."""
    ok = install_cap(env, "001")
    if not ok:
        return TestResult("tool_001_install_verify", False, "CAP-001 install failed")

    # Install TOOL-001
    from ambientrag.tools.tool_001_search_metrics import install as t_install, verify as t_verify

    state = env.load_state()
    ok = t_install.install(state)
    if not ok:
        return TestResult("tool_001_install_verify", False, "TOOL-001 install returned False")

    success, msg = t_verify.verify(state)
    if not success:
        return TestResult("tool_001_install_verify", False, f"TOOL-001 verify failed: {msg}")

    # Double-check table exists via backend
    from ambientrag.db import get_backend
    backend = get_backend(state)
    try:
        backend.connect()
        if not backend.table_exists("search_metrics"):
            return TestResult("tool_001_install_verify", False, "search_metrics table not found")
    finally:
        backend.close()

    return TestResult("tool_001_install_verify", True, "TOOL-001 installed, search_metrics table exists")


def test_tool_001_sqlite(env: TestEnv) -> TestResult:
    """TOOL-001 install on T0 SQLite creates search_metrics table."""
    ok = install_cap(env, "001")
    if not ok:
        return TestResult("tool_001_sqlite", False, "CAP-001 install failed on T0")

    from ambientrag.tools.tool_001_search_metrics import install as t_install, verify as t_verify

    state = env.load_state()
    ok = t_install.install(state)
    if not ok:
        return TestResult("tool_001_sqlite", False, "TOOL-001 install returned False on T0")

    success, msg = t_verify.verify(state)
    if not success:
        return TestResult("tool_001_sqlite", False, f"TOOL-001 verify failed on T0: {msg}")

    # Verify uninstall works
    from ambientrag.tools.tool_001_search_metrics import uninstall as t_uninstall
    ok = t_uninstall.uninstall(state)
    if not ok:
        return TestResult("tool_001_sqlite", False, "TOOL-001 uninstall failed on T0")

    success, msg = t_verify.verify(state)
    if success:
        return TestResult("tool_001_sqlite", False, "search_metrics still exists after uninstall")

    return TestResult("tool_001_sqlite", True, "TOOL-001 install/verify/uninstall works on T0 SQLite")


def test_bench_runs(env: TestEnv) -> TestResult:
    """Bench command runs queries without crashing."""
    ok = install_cap(env, "001")
    if not ok:
        return TestResult("bench_runs", False, "CAP-001 install failed")

    # Install TOOL-001 for --save
    from ambientrag.tools.tool_001_search_metrics import install as t_install
    state = env.load_state()
    ok = t_install.install(state)
    if not ok:
        return TestResult("bench_runs", False, "TOOL-001 install failed")

    # Run bench in-process
    from ambientrag.bench import run_benchmark, save_metrics

    try:
        results, stats, chunk_count = run_benchmark(state, num_queries=3)
    except Exception as e:
        return TestResult("bench_runs", False, f"run_benchmark crashed: {e}")

    if len(results) != 3:
        return TestResult("bench_runs", False, f"Expected 3 results, got {len(results)}")

    # All latencies should be non-negative
    for q, lat, cnt in results:
        if lat < 0:
            return TestResult("bench_runs", False, f"Negative latency for '{q}': {lat}")

    # Stats should have expected keys
    for key in ("p50", "p90", "p99", "avg", "min", "max"):
        if key not in stats:
            return TestResult("bench_runs", False, f"Missing stat: {key}")

    # Test save
    try:
        saved = save_metrics(state, results, chunk_count)
    except Exception as e:
        return TestResult("bench_runs", False, f"save_metrics crashed: {e}")

    if saved != 3:
        return TestResult("bench_runs", False, f"Expected 3 saved, got {saved}")

    # Verify rows in search_metrics
    from ambientrag.db import get_backend
    backend = get_backend(state)
    try:
        backend.connect()
        count = backend.count_rows("search_metrics")
        if count != 3:
            return TestResult("bench_runs", False, f"Expected 3 rows in search_metrics, got {count}")
    finally:
        backend.close()

    return TestResult("bench_runs", True,
                      f"Bench ran 3 queries (avg {stats['avg']:.1f}ms), saved to metrics")


# ── Tool registry tests ──────────────────────────────────────────────

def test_tool_list(env: TestEnv) -> TestResult:
    """Tool registry loads manifest and returns all tools."""
    from ambientrag.tools import registry as tool_registry

    all_tools = tool_registry.get_all_tools()
    if "001" not in all_tools:
        return TestResult("tool_list", False, "TOOL-001 not in registry")
    if "002" not in all_tools:
        return TestResult("tool_list", False, "TOOL-002 not in registry")
    if all_tools["001"]["name"] != "Search Metrics":
        return TestResult("tool_list", False, f"TOOL-001 name wrong: {all_tools['001']['name']}")
    if all_tools["002"]["name"] != "Token Hygiene":
        return TestResult("tool_list", False, f"TOOL-002 name wrong: {all_tools['002']['name']}")
    if not all_tools["001"].get("auto_install"):
        return TestResult("tool_list", False, "TOOL-001 should be auto_install")
    if all_tools["002"].get("auto_install"):
        return TestResult("tool_list", False, "TOOL-002 should NOT be auto_install")

    # Test normalize
    nid = tool_registry.normalize_tool_id("1")
    if nid != "001":
        return TestResult("tool_list", False, f"normalize('1') = {nid}, expected '001'")

    # Test get_tool
    t = tool_registry.get_tool("002")
    if t is None or t["name"] != "Token Hygiene":
        return TestResult("tool_list", False, f"get_tool('002') failed: {t}")

    return TestResult("tool_list", True, f"Tool registry has {len(all_tools)} tools, all metadata correct")


# ── Test runner ───────────────────────────────────────────────────────

# Tests that need their own DB (schema changes)
DB_TESTS: list[tuple[str, Callable[[TestEnv], TestResult], int]] = [
    ("test_001_foundation",          test_001_foundation,          1),
    ("test_001_idempotent",          test_001_idempotent,          1),
    ("test_001_then_004",            test_001_then_004,            1),
    ("test_001_002_003_chain",       test_001_002_003_chain,       1),
    ("test_all_compatible_caps",     test_all_compatible_caps,     1),
    ("test_004_idempotent",          test_004_idempotent,          1),
    ("test_full_cycle",              test_full_cycle_install_verify_status, 1),
    # Lifecycle tests
    ("test_uninstall_leaf_cap",         test_uninstall_leaf_cap,          1),
    ("test_uninstall_idempotent",       test_uninstall_idempotent,        1),
    ("test_uninstall_with_hard_dep",    test_uninstall_with_hard_dep,     1),
    ("test_uninstall_cascade",          test_uninstall_cascade,           1),
    ("test_disable_enable_cycle",       test_disable_enable_cycle,        1),
    ("test_disable_preserves_schema",   test_disable_preserves_schema,    1),
    ("test_cannot_disable_001",         test_cannot_disable_001,          1),
    ("test_reinstall_after_uninstall",  test_install_reinstall_after_uninstall, 1),
    # TOOL-001 tests
    ("test_tool_001_install_verify",    test_tool_001_install_verify,          1),
    ("test_bench_runs",                 test_bench_runs,                       1),
]

# Tests that only check logic (no DB needed — use a shared env)
LOGIC_TESTS: list[tuple[str, Callable[[TestEnv], TestResult]]] = [
    ("test_003_without_002_fails",      test_003_without_002_fails),
    ("test_002_without_001_fails",      test_002_without_001_fails),
    ("test_005_tier_rejection",         test_005_tier_rejection),
    ("test_005_tier2_allowed",          test_005_tier2_allowed),
    ("test_install_order_resolves_deps", test_install_order_resolves_deps),
    ("test_install_order_skips_installed", test_install_order_skips_installed),
    ("test_normalize_cap_ids",          test_normalize_cap_ids),
    # Lifecycle logic tests
    ("test_reverse_deps_hard_and_soft", test_reverse_deps_hard_and_soft),
    ("test_enhances_field_in_manifest", test_enhances_field_in_manifest),
    ("test_packaging_smoke",             test_packaging_smoke),
    ("test_tool_list",                   test_tool_list),
    # Companion doc tests
    ("test_companion_001_only",             test_companion_generation_001_only),
    ("test_companion_with_002",             test_companion_generation_with_002),
    ("test_companion_all_caps",             test_companion_generation_with_all),
    ("test_cap_install_auto_refresh",       test_cap_install_auto_refreshes_companion),
    # T0 SQLite backend tests (no Postgres DB needed)
    ("test_sqlite_backend_create_verify",   test_sqlite_backend_create_verify),
    ("test_sqlite_backend_add_drop_column", test_sqlite_backend_add_drop_column),
]

# T0 tests that need their own environment with tier=0
T0_TESTS: list[tuple[str, Callable[[TestEnv], TestResult]]] = [
    ("test_t0_init_no_postgres",        test_t0_init_no_postgres),
    ("test_t0_cap_004_on_sqlite",       test_t0_cap_004_on_sqlite),
    ("test_migration_status",           test_migration_status),
    ("test_tool_001_sqlite",            test_tool_001_sqlite),
]


def run_tests(keep: bool = False, filter_name: str | None = None):
    suite = TestSuite()

    print(f"\n{'─' * 60}")
    print(f"  AmbientRAG CAP QA Suite")
    print(f"  DB prefix: {DB_PREFIX}*")
    print(f"  Keep artifacts: {keep}")
    if filter_name:
        print(f"  Filter: {filter_name}")
    print(f"{'─' * 60}\n")

    # ── Logic tests (fast, no DB) ──
    print("Logic tests (no DB):")
    for name, fn in LOGIC_TESTS:
        if filter_name and filter_name not in name:
            continue
        try:
            with test_env(name, keep=keep) as env:
                import time
                t0 = time.monotonic()
                result = fn(env)
                result.duration_ms = (time.monotonic() - t0) * 1000
                suite.record(result)
        except Exception as e:
            suite.record(TestResult(name, False, f"Exception: {e}"))

    # ── T0 SQLite tests (no Postgres needed) ──
    print("\nT0 SQLite tests (no Postgres):")
    for name, fn in T0_TESTS:
        if filter_name and filter_name not in name:
            continue
        try:
            with test_env(name.replace("test_", ""), tier=0, keep=keep) as env:
                import time
                t0 = time.monotonic()
                result = fn(env)
                result.duration_ms = (time.monotonic() - t0) * 1000
                suite.record(result)
        except Exception as e:
            suite.record(TestResult(name, False, f"Exception: {e}\n{traceback.format_exc()}"))

    # ── DB tests (each gets its own database) ──
    print("\nDB tests (isolated database each):")
    for name, fn, tier in DB_TESTS:
        if filter_name and filter_name not in name:
            continue
        try:
            with test_env(name.replace("test_", ""), tier=tier, keep=keep) as env:
                import time
                t0 = time.monotonic()
                result = fn(env)
                result.duration_ms = (time.monotonic() - t0) * 1000
                suite.record(result)
        except Exception as e:
            suite.record(TestResult(name, False, f"Exception: {e}\n{traceback.format_exc()}"))

    return suite.summary()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AmbientRAG CAP QA Suite")
    parser.add_argument("--keep", action="store_true", help="Keep test artifacts for debugging")
    parser.add_argument("-k", "--filter", type=str, help="Run only tests matching this substring")
    args = parser.parse_args()

    # Ensure we can import the project
    sys.path.insert(0, str(PROJECT_ROOT))

    exit_code = run_tests(keep=args.keep, filter_name=args.filter)
    sys.exit(exit_code)
