"""AmbientRAG doctor — system check and tier recommendation."""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ambientrag.utils import check_port, print_info, print_success, print_warning, print_error

console = Console()


def _check_command(cmd: str) -> tuple[bool, str]:
    """Check if a command exists and return its version if possible."""
    path = shutil.which(cmd)
    if not path:
        return False, "not found"
    try:
        result = subprocess.run(
            [cmd, "--version"],
            capture_output=True, text=True, timeout=5,
        )
        version = result.stdout.strip().split("\n")[0] if result.stdout else result.stderr.strip().split("\n")[0]
        return True, version
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return True, f"found at {path}"


def _check_python() -> tuple[bool, str]:
    """Check Python version."""
    v = sys.version.split()[0]
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 10)
    hint = "" if ok else " (3.10+ required — brew install python@3.12)"
    return ok, f"Python {v}{hint}"


def _check_postgres() -> tuple[bool, str]:
    """Check if PostgreSQL is available and running."""
    found, version = _check_command("psql")
    if not found:
        return False, "not installed"

    # Check if it's running
    try:
        result = subprocess.run(
            ["pg_isready"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return True, f"{version} (running)"
        return True, f"{version} (installed but not running)"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return True, f"{version} (can't check if running)"


def _check_brew() -> tuple[bool, str]:
    """Check if Homebrew is available."""
    return _check_command("brew")


def _check_docker() -> tuple[bool, str]:
    """Check if Docker is available and running."""
    found, version = _check_command("docker")
    if not found:
        return False, "not installed"

    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return True, f"{version} (running)"
        return True, f"{version} (installed but daemon not running)"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return True, f"{version} (can't check daemon)"


def _check_git() -> tuple[bool, str]:
    """Check if git is available."""
    return _check_command("git")


def _find_models(vault_path: Path | None) -> dict[str, Path | None]:
    """Search for embedding and reranker models."""
    models = {"harrier-0.6b": None, "bge-reranker-v2-m3": None}

    search_dirs = [
        Path("~/.cache/huggingface/hub").expanduser(),
        Path("~/Documents/ambientrag/_system/models").expanduser(),
        Path("~/.ambientrag/models").expanduser(),
    ]
    if vault_path:
        search_dirs.insert(0, vault_path / "_system" / "models")

    for model_name in models:
        marker = "model.safetensors"
        alt_marker = "pytorch_model.bin"

        for search_dir in search_dirs:
            candidate = search_dir / model_name
            if (candidate / marker).exists() or (candidate / alt_marker).exists():
                models[model_name] = candidate
                break

        # Also check HuggingFace cache naming convention
        if models[model_name] is None:
            hf_cache = Path("~/.cache/huggingface/hub").expanduser()
            for hf_name in [f"models--microsoft--harrier-oss-v1-0.6b", f"models--BAAI--{model_name}"]:
                snapshots = hf_cache / hf_name / "snapshots"
                if snapshots.exists():
                    for snapshot in snapshots.iterdir():
                        if (snapshot / marker).exists() or (snapshot / alt_marker).exists():
                            models[model_name] = snapshot
                            break

    return models


def _check_sqlite_vec() -> tuple[bool, str]:
    """Check if sqlite-vec is importable."""
    try:
        import sqlite_vec
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        version = db.execute("SELECT vec_version()").fetchone()[0]
        db.close()
        return True, f"sqlite-vec {version}"
    except ImportError:
        return False, "not installed (pip install sqlite-vec)"
    except Exception as e:
        return False, f"import ok but load failed: {e}"


def _recommend_tier(checks: dict) -> int:
    """Recommend a tier based on what's available."""
    if checks["docker"][0] and checks["postgres"][0]:
        return 3 if checks["models"]["harrier-0.6b"] and checks["models"]["bge-reranker-v2-m3"] else 2
    if checks["postgres"][0]:
        return 1
    return 0


@click.command()
def doctor():
    """Check system prerequisites and recommend a tier."""
    console.print(Panel(
        f"[bold]System Check[/bold]\n"
        f"OS: {platform.system()} {platform.machine()}\n"
        f"Host: {platform.node()}",
        title="ambientrag doctor",
    ))

    # Run all checks
    checks = {
        "python": _check_python(),
        "postgres": _check_postgres(),
        "sqlite_vec": _check_sqlite_vec(),
        "brew": _check_brew(),
        "docker": _check_docker(),
        "git": _check_git(),
    }

    # Check models
    try:
        from ambientrag.state import get_vault_path
        vault_path = get_vault_path()
    except Exception:
        vault_path = None
    models = _find_models(vault_path)
    checks["models"] = models

    # Prerequisites table
    t = Table(title="Prerequisites", show_header=True)
    t.add_column("Component")
    t.add_column("Status")
    t.add_column("Details")

    for name, (ok, detail) in [
        ("Python 3.10+", checks["python"]),
        ("PostgreSQL", checks["postgres"]),
        ("sqlite-vec", checks["sqlite_vec"]),
        ("Homebrew", checks["brew"]),
        ("Docker", checks["docker"]),
        ("Git", checks["git"]),
    ]:
        icon = "[green]OK[/green]" if ok else "[dim]--[/dim]"
        t.add_row(name, icon, detail)

    console.print(t)

    # Models table
    t2 = Table(title="Models", show_header=True)
    t2.add_column("Model")
    t2.add_column("Status")
    t2.add_column("Location")

    for model_name, path in models.items():
        if path:
            t2.add_row(model_name, "[green]found[/green]", str(path))
        else:
            t2.add_row(model_name, "[dim]not found[/dim]", "")

    console.print(t2)

    # Services table
    console.print("")
    t3 = Table(title="Services", show_header=True)
    t3.add_column("Port")
    t3.add_column("Service")
    t3.add_column("Status")

    for port, svc in [(8100, "MCP Server"), (8787, "Embed Server"), (8788, "Reranker")]:
        up = check_port(port)
        t3.add_row(str(port), svc, "[green]UP[/green]" if up else "[dim]DOWN[/dim]")

    console.print(t3)

    # Tier recommendation
    tier = _recommend_tier(checks)
    tier_info = {
        0: ("PGLite", "Zero external deps, ~2-3s queries"),
        1: ("Brew Postgres", "Fast local search, ~200-500ms"),
        2: ("Docker", "Postgres + reranker, ~100-200ms"),
        3: ("Full", "Everything local + GPU, ~50-100ms"),
    }
    name, desc = tier_info[tier]

    console.print("")
    console.print(Panel(
        f"[bold]Recommended tier: T{tier} — {name}[/bold]\n"
        f"{desc}\n\n"
        f"  ambientrag init --tier {tier} --vault-path ~/ambientrag",
        title="Recommendation",
    ))

    # Platform skills audit
    skills_dirs = [
        Path("~/.gemini/antigravity/skills").expanduser(),
    ]
    suspect_files: list[tuple[str, str]] = []
    for skills_dir in skills_dirs:
        if not skills_dir.exists():
            continue
        for f in skills_dir.rglob("*"):
            if f.is_file() and f.name != "SKILL.md":
                # Check if it references vault paths or synapse
                try:
                    text = f.read_text(errors="ignore")
                    reasons = []
                    if "synapse" in text.lower():
                        reasons.append("references 'synapse' (old name)")
                    if "Documents/synapse" in text:
                        reasons.append("hardcoded ~/Documents/synapse path")
                    if "obsidian-vault" in text and "vault_path" not in text:
                        reasons.append("hardcoded obsidian-vault path")
                    if ".vault-path" in text:
                        reasons.append("uses deprecated ~/.vault-path")
                    if reasons:
                        suspect_files.append((str(f), "; ".join(reasons)))
                except Exception:
                    pass

    if suspect_files:
        console.print("")
        t4 = Table(title="[yellow]Platform Skills Audit[/yellow]", show_header=True)
        t4.add_column("File")
        t4.add_column("Issue")
        for fpath, reason in suspect_files:
            t4.add_row(fpath, f"[yellow]{reason}[/yellow]")
        console.print(t4)
        print_warning("These files may cause notes to save to the wrong location.")
        print_warning("Consider removing them — save_note MCP tool replaces script-based saving.")

    # Actionable suggestions
    if not checks["postgres"][0]:
        console.print("")
        print_info("Want T1? Install PostgreSQL:")
        print_info("  brew install postgresql@17 && brew services start postgresql@17")

    if not checks["docker"][0] and checks["postgres"][0]:
        console.print("")
        print_info("Want T2+? Install Docker:")
        print_info("  https://docs.docker.com/desktop/install/mac-install/")

    if not models["harrier-0.6b"]:
        console.print("")
        print_info("Embedding model (needed for search indexing):")
        print_info("  https://huggingface.co/microsoft/harrier-oss-v1-0.6b")
