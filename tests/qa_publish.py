#!/usr/bin/env python3
"""
Pre-publish QA for AmbientRAG PyPI package.

Validates metadata, structure, codename leaks, secrets, build artifacts,
and CAP-by-CAP requirements. Runs deterministically — no network calls
unless --check-pypi is passed.

Usage:
    python tests/qa_publish.py --profile prod
    python tests/qa_publish.py --profile test
    python tests/qa_publish.py --profile prod --check-pypi --build
    python tests/qa_publish.py --profile prod --cap 001
    python tests/qa_publish.py --profile prod --cap 001 002 003
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from dataclasses import dataclass, field

# ── Resolve project root ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
PKG_DIR = ROOT / "ambientrag"
CAPS_DIR = PKG_DIR / "caps"
TOOLS_DIR = PKG_DIR / "tools"

# ── Profiles ──────────────────────────────────────────────────────────

PROFILES = {
    "test": {
        "expected_name": "skunkrag",
        "expected_cli": "skunkrag",
        "forbidden_strings": ["ambientrag.com", "ambientrag.tech"],
        "required_license": None,  # lenient
        "check_urls": False,
        "leak_term": "ambientrag",  # this should NOT appear in README title
    },
    "prod": {
        "expected_name": "ambientrag",
        "expected_cli": "ambientrag",
        "forbidden_strings": ["skunkrag"],
        "required_license": "Apache-2.0",
        "check_urls": True,
        "leak_term": "skunkrag",  # this should NOT appear anywhere
    },
}

# ── CAP requirement definitions ───────────────────────────────────────
# Each CAP declares what files, modules, and DB artifacts it expects.
# The QA script validates these exist BEFORE publishing.

CAP_REQUIREMENTS = {
    "001": {
        "name": "Vector Search",
        "requires_caps": [],
        "directory": "cap_001_vector_search",
        "required_files": [
            "__init__.py",
            "install.py",
            "verify.py",
            "schema.sql",
        ],
        "optional_files": ["uninstall.py"],
        "importable_modules": [
            "ambientrag.caps.cap_001_vector_search",
            "ambientrag.caps.cap_001_vector_search.install",
            "ambientrag.caps.cap_001_vector_search.verify",
        ],
        "manifest_entry": {
            "requires": [],
            "tier_min": 0,
        },
        "verify_function": True,  # must have verify(state) -> (bool, str)
    },
    "002": {
        "name": "Enrichment Pipeline",
        "requires_caps": ["001"],
        "directory": "cap_002_enrichment",
        "required_files": [
            "__init__.py",
            "install.py",
            "verify.py",
        ],
        "optional_files": ["uninstall.py"],
        "importable_modules": [
            "ambientrag.caps.cap_002_enrichment",
            "ambientrag.caps.cap_002_enrichment.install",
            "ambientrag.caps.cap_002_enrichment.verify",
        ],
        "manifest_entry": {
            "requires": ["001"],
            "tier_min": 0,
        },
        "verify_function": True,
    },
    "003": {
        "name": "Tiered Retrieval",
        "requires_caps": ["002"],
        "directory": "cap_003_tiered_retrieval",
        "required_files": [
            "__init__.py",
            "install.py",
            "verify.py",
        ],
        "optional_files": ["uninstall.py"],
        "importable_modules": [
            "ambientrag.caps.cap_003_tiered_retrieval",
            "ambientrag.caps.cap_003_tiered_retrieval.install",
            "ambientrag.caps.cap_003_tiered_retrieval.verify",
        ],
        "manifest_entry": {
            "requires": ["002"],
            "tier_min": 0,
        },
        "verify_function": True,
    },
    "004": {
        "name": "Temporal Scoring",
        "requires_caps": ["001"],
        "directory": "cap_004_temporal_scoring",
        "required_files": [
            "__init__.py",
            "install.py",
            "verify.py",
        ],
        "optional_files": ["uninstall.py"],
        "importable_modules": [
            "ambientrag.caps.cap_004_temporal_scoring",
            "ambientrag.caps.cap_004_temporal_scoring.install",
            "ambientrag.caps.cap_004_temporal_scoring.verify",
        ],
        "manifest_entry": {
            "requires": ["001"],
            "tier_min": 0,
        },
        "verify_function": True,
    },
    "005": {
        "name": "Cross-Encoder Reranker",
        "requires_caps": ["001"],
        "directory": "cap_005_reranker",
        "required_files": [
            "__init__.py",
            "install.py",
        ],
        "optional_files": ["uninstall.py", "verify.py"],
        "importable_modules": [
            "ambientrag.caps.cap_005_reranker",
        ],
        "manifest_entry": {
            "requires": ["001"],
            "tier_min": 2,
        },
        "verify_function": False,
    },
}

# ── Patterns that should never be in published source ─────────────────

SECRET_PATTERNS = [
    r"(?i)api[_-]?key\s*=\s*['\"][a-zA-Z0-9_\-]{20,}",
    r"(?i)token\s*=\s*['\"]pypi-[a-zA-Z0-9_\-]+",
    r"(?i)password\s*=\s*['\"][^'\"]{8,}",
    r"(?i)secret\s*=\s*['\"][^'\"]{8,}",
    r"sk-[a-zA-Z0-9]{20,}",  # OpenAI-style keys
    r"ghp_[a-zA-Z0-9]{36}",  # GitHub PATs
    r"pypi-[a-zA-Z0-9_\-]{50,}",  # PyPI tokens
]

HARDCODED_PATH_PATTERNS = [
    r"/Users/\w+/",
    r"/home/\w+/",
    r"/private/tmp/",
    r"C:\\Users\\",
]

# ── Result tracking ──────────────────────────────────────────────────

@dataclass
class Check:
    name: str
    passed: bool
    message: str
    section: str


@dataclass
class QAResult:
    checks: list[Check] = field(default_factory=list)

    def add(self, section: str, name: str, passed: bool, message: str = ""):
        self.checks.append(Check(name=name, passed=passed, message=message, section=section))

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    def print_report(self):
        sections: dict[str, list[Check]] = {}
        for c in self.checks:
            sections.setdefault(c.section, []).append(c)

        print()
        for section, checks in sections.items():
            print(f"  {section}")
            for c in checks:
                icon = "\033[32m✓\033[0m" if c.passed else "\033[31m✗\033[0m"
                msg = f" — {c.message}" if c.message else ""
                print(f"    {icon} {c.name}{msg}")
            print()

        total = len(self.checks)
        failed = self.failed_count
        if failed == 0:
            print(f"  \033[32mAll {total} checks passed.\033[0m")
        else:
            print(f"  \033[31m{failed} of {total} checks failed. Fix before publishing.\033[0m")
        print()


# ── Helpers ───────────────────────────────────────────────────────────

def parse_pyproject() -> dict:
    """Minimal TOML parser — just enough for pyproject.toml validation."""
    text = PYPROJECT.read_text()
    # We only need [project] fields. Use tomllib if available (3.11+), else regex.
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            tomllib = None

    if tomllib:
        return tomllib.loads(text)

    # Fallback: regex extraction for critical fields
    result = {"project": {}, "tool": {}}

    def extract(pattern, text, default=None):
        m = re.search(pattern, text)
        return m.group(1) if m else default

    result["project"]["name"] = extract(r'^name\s*=\s*"([^"]+)"', text)
    result["project"]["version"] = extract(r'^version\s*=\s*"([^"]+)"', text)
    result["project"]["license"] = extract(r'^license\s*=\s*"([^"]+)"', text)
    result["project"]["requires-python"] = extract(r'^requires-python\s*=\s*"([^"]+)"', text)
    result["project"]["readme"] = extract(r'^readme\s*=\s*"([^"]+)"', text)
    result["project"]["description"] = extract(r'^description\s*=\s*"([^"]+)"', text)

    # Extract classifiers
    classifiers = []
    in_classifiers = False
    for line in text.splitlines():
        if line.strip().startswith("classifiers"):
            in_classifiers = True
            continue
        if in_classifiers:
            if line.strip() == "]":
                in_classifiers = False
                continue
            m = re.match(r'\s*"([^"]+)"', line)
            if m:
                classifiers.append(m.group(1))
    result["project"]["classifiers"] = classifiers

    # Extract scripts
    scripts = {}
    in_scripts = False
    for line in text.splitlines():
        if line.strip() == "[project.scripts]":
            in_scripts = True
            continue
        if in_scripts:
            if line.strip().startswith("["):
                break
            m = re.match(r'^(\w[\w-]*)\s*=\s*"([^"]+)"', line.strip())
            if m:
                scripts[m.group(1)] = m.group(2)
    result["project"]["scripts"] = scripts

    # Extract URLs
    urls = {}
    in_urls = False
    for line in text.splitlines():
        if line.strip() == "[project.urls]":
            in_urls = True
            continue
        if in_urls:
            if line.strip().startswith("["):
                break
            m = re.match(r'^(\w+)\s*=\s*"([^"]+)"', line.strip())
            if m:
                urls[m.group(1)] = m.group(2)
    result["project"]["urls"] = urls

    # Extract authors email
    authors_match = re.search(r'email\s*=\s*"([^"]+)"', text)
    if authors_match:
        result["project"]["_author_email"] = authors_match.group(1)

    return result


def semver_valid(version: str) -> bool:
    return bool(re.match(r"^\d+\.\d+\.\d+([a-zA-Z0-9\.\-]+)?$", version))


def scan_files_for_patterns(directory: Path, patterns: list[str],
                            extensions: tuple = (".py", ".toml", ".cfg", ".md", ".txt", ".sh"),
                            exclude_dirs: set | None = None) -> list[tuple[str, int, str]]:
    """Scan files for regex patterns. Returns [(filepath, line_no, matched_text), ...]."""
    if exclude_dirs is None:
        exclude_dirs = {"__pycache__", ".git", ".venv", "node_modules", "build", "dist", "*.egg-info"}
    hits = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.endswith(".egg-info")]
        for fname in files:
            if not any(fname.endswith(ext) for ext in extensions):
                continue
            fpath = Path(root) / fname
            try:
                text = fpath.read_text(errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                for pat in patterns:
                    if re.search(pat, line):
                        hits.append((str(fpath.relative_to(directory)), i, line.strip()[:120]))
    return hits


# ── Check sections ────────────────────────────────────────────────────

def check_metadata(result: QAResult, profile: dict, config: dict):
    """Validate pyproject.toml metadata."""
    section = "Metadata"
    project = config.get("project", {})

    # Name
    name = project.get("name")
    expected = profile["expected_name"]
    result.add(section, f"name = {name}", name == expected,
               f"expected '{expected}'" if name != expected else "")

    # Version
    version = project.get("version", "")
    result.add(section, f"version = {version}", semver_valid(version),
               "invalid semver" if not semver_valid(version) else "")

    # License
    license_val = project.get("license", "")
    if profile["required_license"]:
        result.add(section, f"license = {license_val}",
                   license_val == profile["required_license"],
                   f"expected {profile['required_license']}" if license_val != profile["required_license"] else "")

    # requires-python vs classifiers
    req_python = project.get("requires-python", "")
    classifiers = project.get("classifiers", [])
    py_classifiers = [c for c in classifiers if "Python :: 3." in c]
    if req_python:
        min_match = re.search(r">=\s*3\.(\d+)", req_python)
        if min_match:
            min_minor = int(min_match.group(1))
            bad_classifiers = []
            for c in py_classifiers:
                ver_match = re.search(r"Python :: 3\.(\d+)", c)
                if ver_match and int(ver_match.group(1)) < min_minor:
                    bad_classifiers.append(c)
            result.add(section, "classifiers match requires-python",
                       len(bad_classifiers) == 0,
                       f"remove: {bad_classifiers}" if bad_classifiers else "")

    # Description
    desc = project.get("description", "")
    result.add(section, "description present", bool(desc), "empty description" if not desc else "")

    # README
    readme = project.get("readme", "README.md")
    readme_path = ROOT / readme if readme else ROOT / "README.md"
    readme_exists = readme_path.exists()
    result.add(section, f"README ({readme})", readme_exists,
               "file not found" if not readme_exists else f"{readme_path.stat().st_size / 1024:.1f} KB")

    # Author email
    email = project.get("_author_email", "")
    if not email:
        # Try nested authors structure
        authors = project.get("authors", [])
        if authors and isinstance(authors[0], dict):
            email = authors[0].get("email", "")
    placeholder_emails = ["example.com", "placeholder", "changeme", "todo"]
    email_ok = bool(email) and not any(p in email.lower() for p in placeholder_emails)
    result.add(section, f"author email ({email or 'missing'})", email_ok,
               "placeholder or missing email" if not email_ok else "")


def check_entry_points(result: QAResult, profile: dict, config: dict):
    """Validate CLI entry points."""
    section = "Entry Points"
    project = config.get("project", {})
    scripts = project.get("scripts", {})

    expected_cli = profile["expected_cli"]
    has_entry = expected_cli in scripts
    result.add(section, f"'{expected_cli}' entry point", has_entry,
               f"found: {list(scripts.keys())}" if not has_entry else scripts.get(expected_cli, ""))

    # Check that wrong entry point is NOT present
    wrong_cli = "ambientrag" if expected_cli == "skunkrag" else "skunkrag"
    has_wrong = wrong_cli in scripts
    result.add(section, f"no '{wrong_cli}' entry point", not has_wrong,
               f"codename leak — '{wrong_cli}' still in scripts" if has_wrong else "")

    # Check target is importable
    if has_entry:
        target = scripts[expected_cli]
        module_path, _, func_name = target.rpartition(":")
        try:
            mod = importlib.import_module(module_path)
            has_func = hasattr(mod, func_name)
            result.add(section, f"target importable ({target})", has_func,
                       f"'{func_name}' not found in {module_path}" if not has_func else "")
        except Exception as e:
            result.add(section, f"target importable ({target})", False, str(e))


def check_package_structure(result: QAResult):
    """Validate package files and version consistency."""
    section = "Package Structure"

    # __init__.py with __version__
    init_file = PKG_DIR / "__init__.py"
    result.add(section, "__init__.py exists", init_file.exists())

    if init_file.exists():
        init_text = init_file.read_text()
        ver_match = re.search(r'__version__\s*=\s*["\']([^"\']+)', init_text)
        if ver_match:
            pkg_version = ver_match.group(1)
            toml_text = PYPROJECT.read_text()
            toml_ver_match = re.search(r'^version\s*=\s*"([^"]+)"', toml_text, re.MULTILINE)
            toml_version = toml_ver_match.group(1) if toml_ver_match else "?"
            result.add(section, f"__version__ ({pkg_version}) matches pyproject ({toml_version})",
                       pkg_version == toml_version)
        else:
            result.add(section, "__version__ defined in __init__.py", False, "not found")

    # __main__.py
    result.add(section, "__main__.py exists", (PKG_DIR / "__main__.py").exists(),
               "allows 'python -m ambientrag'")

    # Manifest files
    caps_manifest = CAPS_DIR / "manifest.json"
    result.add(section, "caps/manifest.json", caps_manifest.exists())
    tools_manifest = TOOLS_DIR / "manifest.json"
    result.add(section, "tools/manifest.json", tools_manifest.exists())

    # No __pycache__ in package (would indicate dirty build)
    pycache_dirs = list(PKG_DIR.rglob("__pycache__"))
    result.add(section, "no __pycache__ in package", len(pycache_dirs) == 0,
               f"{len(pycache_dirs)} found — run 'find . -name __pycache__ -exec rm -rf {{}} +'" if pycache_dirs else "")

    # .gitignore
    gitignore = ROOT / ".gitignore"
    if gitignore.exists():
        gi_text = gitignore.read_text()
        expected_entries = ["dist/", "*.egg-info", "__pycache__", ".venv"]
        missing = [e for e in expected_entries if e not in gi_text]
        result.add(section, ".gitignore coverage", len(missing) == 0,
                   f"missing: {missing}" if missing else "")
    else:
        result.add(section, ".gitignore exists", False, "no .gitignore found")


def check_codename_leaks(result: QAResult, profile: dict):
    """Scan for codename leaks — wrong name appearing in source."""
    section = "Codename Leaks"
    leak_term = profile["leak_term"]

    # Scan all source files for the forbidden term
    hits = scan_files_for_patterns(ROOT, [re.escape(leak_term)],
                                   extensions=(".py", ".toml", ".cfg", ".md", ".txt", ".sh", ".yml", ".yaml"))
    # Filter out this QA script itself and test files
    hits = [(f, l, t) for f, l, t in hits
            if not f.startswith("tests/") and f != "qa_publish.py"]

    result.add(section, f"no '{leak_term}' in source", len(hits) == 0,
               f"{len(hits)} occurrences found" if hits else "")
    for fpath, line_no, text in hits[:5]:
        result.add(section, f"  {fpath}:{line_no}", False, text[:80])

    # Scan for forbidden strings (e.g. ambientrag.com in test profile)
    for forbidden in profile.get("forbidden_strings", []):
        fhits = scan_files_for_patterns(ROOT, [re.escape(forbidden)],
                                         extensions=(".py", ".toml", ".cfg"))
        fhits = [(f, l, t) for f, l, t in fhits if not f.startswith("tests/")]
        result.add(section, f"no '{forbidden}' in config/source", len(fhits) == 0,
                   f"{len(fhits)} occurrences" if fhits else "")

    # Hardcoded paths
    path_hits = scan_files_for_patterns(PKG_DIR, HARDCODED_PATH_PATTERNS,
                                         extensions=(".py",))
    result.add(section, "no hardcoded user paths in package", len(path_hits) == 0,
               f"{len(path_hits)} found" if path_hits else "")
    for fpath, line_no, text in path_hits[:3]:
        result.add(section, f"  {fpath}:{line_no}", False, text[:80])


def check_secrets(result: QAResult):
    """Scan for leaked secrets and sensitive files."""
    section = "Secrets"

    # Pattern scan
    hits = scan_files_for_patterns(PKG_DIR, SECRET_PATTERNS, extensions=(".py", ".toml", ".cfg", ".env"))
    result.add(section, "no secrets in source", len(hits) == 0,
               f"{len(hits)} potential secrets" if hits else "")
    for fpath, line_no, text in hits[:3]:
        result.add(section, f"  {fpath}:{line_no}", False, "[redacted — check manually]")

    # Sensitive files that should not be in the tree
    sensitive_files = [".env", ".pypirc", "credentials.json", "token.json", "secrets.yaml"]
    for sf in sensitive_files:
        found = (ROOT / sf).exists()
        result.add(section, f"no {sf} in project root", not found,
                   "remove before publishing" if found else "")


def check_caps(result: QAResult, cap_ids: list[str] | None = None):
    """Validate CAP module structure and manifest consistency."""
    section = "CAP Validation"

    # Load manifest
    manifest_path = CAPS_DIR / "manifest.json"
    if not manifest_path.exists():
        result.add(section, "caps/manifest.json", False, "file not found")
        return

    with manifest_path.open() as f:
        manifest = json.load(f)
    caps_in_manifest = manifest.get("capabilities", {})

    # Determine which CAPs to check
    if cap_ids:
        targets = {cid: CAP_REQUIREMENTS[cid] for cid in cap_ids if cid in CAP_REQUIREMENTS}
    else:
        targets = CAP_REQUIREMENTS

    for cap_id, req in targets.items():
        cap_section = f"CAP-{cap_id} ({req['name']})"

        # Directory exists
        cap_dir = CAPS_DIR / req["directory"]
        result.add(cap_section, "directory exists", cap_dir.exists(),
                   str(cap_dir.relative_to(ROOT)) if not cap_dir.exists() else "")

        if not cap_dir.exists():
            continue

        # Required files
        for fname in req["required_files"]:
            fpath = cap_dir / fname
            result.add(cap_section, f"{fname} present", fpath.exists())

        # Optional files — just note if missing, don't fail
        for fname in req.get("optional_files", []):
            fpath = cap_dir / fname
            if not fpath.exists():
                result.add(cap_section, f"{fname} (optional, missing)", True, "not required")

        # Manifest entry
        if cap_id in caps_in_manifest:
            entry = caps_in_manifest[cap_id]
            expected = req["manifest_entry"]

            # Check requires match
            actual_requires = sorted(entry.get("requires", []))
            expected_requires = sorted(expected.get("requires", []))
            result.add(cap_section, "manifest requires",
                       actual_requires == expected_requires,
                       f"expected {expected_requires}, got {actual_requires}" if actual_requires != expected_requires else "")

            # Check tier_min
            actual_tier = entry.get("tier_min", 0)
            expected_tier = expected.get("tier_min", 0)
            result.add(cap_section, f"manifest tier_min = {actual_tier}",
                       actual_tier == expected_tier,
                       f"expected {expected_tier}" if actual_tier != expected_tier else "")
        else:
            result.add(cap_section, "in manifest.json", False, f"CAP-{cap_id} not in manifest")

        # Importable modules
        for mod_name in req.get("importable_modules", []):
            try:
                importlib.import_module(mod_name)
                result.add(cap_section, f"import {mod_name.split('.')[-1]}", True)
            except Exception as e:
                result.add(cap_section, f"import {mod_name.split('.')[-1]}", False, str(e)[:80])

        # Verify function signature
        if req.get("verify_function"):
            verify_mod = f"ambientrag.caps.{req['directory']}.verify"
            try:
                mod = importlib.import_module(verify_mod)
                has_verify = hasattr(mod, "verify") and callable(mod.verify)
                result.add(cap_section, "verify() function", has_verify,
                           "missing or not callable" if not has_verify else "")
            except Exception as e:
                result.add(cap_section, "verify() function", False, str(e)[:80])

        # Dependency chain — required CAPs must also pass
        for dep_id in req["requires_caps"]:
            dep_dir = CAPS_DIR / CAP_REQUIREMENTS[dep_id]["directory"]
            result.add(cap_section, f"dependency CAP-{dep_id} present", dep_dir.exists(),
                       f"CAP-{cap_id} requires CAP-{dep_id}" if not dep_dir.exists() else "")


def check_build(result: QAResult):
    """Build sdist + wheel and validate artifacts."""
    section = "Build"

    dist_dir = ROOT / "dist"

    # Clean old artifacts
    if dist_dir.exists():
        for f in dist_dir.iterdir():
            f.unlink()

    # Build
    proc = subprocess.run(
        [sys.executable, "-m", "build"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    result.add(section, "python -m build", proc.returncode == 0,
               proc.stderr.strip().splitlines()[-1] if proc.returncode != 0 else "")

    if proc.returncode != 0:
        return

    # Check artifacts exist
    sdists = list(dist_dir.glob("*.tar.gz"))
    wheels = list(dist_dir.glob("*.whl"))
    result.add(section, "sdist produced", len(sdists) > 0,
               sdists[0].name if sdists else "not found")
    result.add(section, "wheel produced", len(wheels) > 0,
               wheels[0].name if wheels else "not found")

    # twine check
    proc = subprocess.run(
        [sys.executable, "-m", "twine", "check", "dist/*"],
        cwd=ROOT, capture_output=True, text=True, shell=False, timeout=30,
    )
    # twine check needs glob expansion — use shell
    proc = subprocess.run(
        f"{sys.executable} -m twine check dist/*",
        cwd=ROOT, capture_output=True, text=True, shell=True, timeout=30,
    )
    result.add(section, "twine check", proc.returncode == 0,
               proc.stdout.strip().splitlines()[-1] if proc.stdout else proc.stderr.strip()[:80])


def check_pypi_version(result: QAResult, profile: dict, config: dict):
    """Check if current version already exists on target PyPI."""
    section = "PyPI"
    import urllib.request
    import urllib.error

    name = config.get("project", {}).get("name", "")
    version = config.get("project", {}).get("version", "")

    if profile["expected_name"] == "skunkrag":
        base_url = "https://test.pypi.org/pypi"
    else:
        base_url = "https://pypi.org/pypi"

    url = f"{base_url}/{name}/{version}/json"
    try:
        urllib.request.urlopen(url, timeout=10)
        result.add(section, f"version {version} not on PyPI", False,
                   f"{version} already published at {base_url}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            result.add(section, f"version {version} available", True, "not yet published")
        else:
            result.add(section, f"PyPI check", False, f"HTTP {e.code}")
    except Exception as e:
        result.add(section, "PyPI check", False, str(e)[:80])


# ── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AmbientRAG Pre-Publish QA")
    parser.add_argument("--profile", choices=["test", "prod"], required=True,
                        help="Validation profile: 'test' (skunkrag) or 'prod' (ambientrag)")
    parser.add_argument("--build", action="store_true",
                        help="Also build sdist + wheel and run twine check")
    parser.add_argument("--check-pypi", action="store_true",
                        help="Check if version exists on PyPI (requires network)")
    parser.add_argument("--cap", nargs="*", metavar="CAP_ID",
                        help="Validate specific CAPs only (e.g. --cap 001 002)")

    args = parser.parse_args()
    profile = PROFILES[args.profile]
    result = QAResult()

    # Header
    display_name = profile["expected_name"]
    print()
    print("  ════════════════════════════════════════════════")
    print(f"  AmbientRAG Pre-Publish QA")
    print(f"  Profile: {args.profile} ({display_name})")
    print("  ════════════════════════════════════════════════")

    # Parse config
    config = parse_pyproject()

    # Run check sections
    check_metadata(result, profile, config)
    check_entry_points(result, profile, config)
    check_package_structure(result)
    check_codename_leaks(result, profile)
    check_secrets(result)

    # CAP validation — always runs (modular)
    cap_ids = args.cap if args.cap else None
    check_caps(result, cap_ids)

    # Optional sections
    if args.build:
        check_build(result)
    if args.check_pypi:
        check_pypi_version(result, profile, config)

    # Report
    result.print_report()
    sys.exit(0 if result.failed_count == 0 else 1)


if __name__ == "__main__":
    main()
