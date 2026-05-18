"""AmbientRAG CLI — modular capability-based RAG installer."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ambientrag import __version__
from ambientrag.caps import registry
from ambientrag.tier import TIERS, can_upgrade, get_tier_info, validate_tier
from ambientrag.utils import check_port, print_error, print_info, print_success, print_warning

import ambientrag.state as _state

console = Console()


@click.group()
@click.version_option(version=__version__)
@click.pass_context
def cli(ctx):
    """AmbientRAG — Modular RAG for Obsidian vaults.

    \b
    Quick start:
      ambientrag doctor
      ambientrag init --vault-path ~/ambientrag
      ambientrag demo seed
      ambientrag bench --save
      ambientrag cap list
      ambientrag connect codex
    """
    pass


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--tier", type=int, default=0, help="Infrastructure tier (0=SQLite, 1=Postgres, 2=Docker, 3=Full)")
@click.option(
    "--vault-path",
    type=click.Path(),
    default=".",
    help="Path to Obsidian vault",
)
def init(tier: int, vault_path: str):
    """Initialize AmbientRAG for an Obsidian vault."""
    resolved = Path(vault_path).resolve()

    if not validate_tier(tier):
        print_error(f"Invalid tier {tier}. Valid tiers: 0-3")
        raise click.Abort()

    if _state.is_initialized():
        existing = _state.get_vault_path()
        if not click.confirm(
            f"Already initialized (vault: {existing}). Re-initialize?", default=False
        ):
            return

    tier_info = get_tier_info(tier)
    console.print(Panel(
        f"[bold]Initializing AmbientRAG[/bold]\n"
        f"Vault: [cyan]{resolved}[/cyan]\n"
        f"Tier:  [yellow]T{tier} — {tier_info.name}[/yellow] ({tier_info.performance})",
        title="ambientrag init",
    ))

    db_url = "postgresql://localhost/ambientrag" if tier >= 1 else None
    db_path = str(resolved / "_system" / "ambientrag.db") if tier == 0 else None

    state = {
        "vault_path": str(resolved),
        "tier": tier,
        "db_url": db_url,
        "db_path": db_path,
        "installed_caps": {},
    }
    _state.save_state(state)
    print_success("State saved")

    # Install CAP-001 automatically
    print_info("Installing CAP-001 (Vector Search)...")
    from ambientrag.caps.cap_001_vector_search import install as install_001, verify as verify_001

    ok = install_001.install(state)
    if ok:
        ok2, msg = verify_001.verify(state)
        if ok2:
            _state.mark_cap_installed("001", "0.1.0")
            print_success(f"CAP-001 installed and verified: {msg}")
        else:
            print_warning(f"CAP-001 installed but verify failed: {msg}")
            print_warning("Run `ambientrag status` after starting PostgreSQL")
    else:
        print_error("CAP-001 install failed — check PostgreSQL is running")

    console.print("")
    console.print(Panel(
        f"[bold green]Initialization complete![/bold green]\n\n"
        f"[bold]Next:[/bold]\n"
        f"  ambientrag demo seed       — drop 5 demo notes into your vault\n"
        f"  ambientrag index           — index them into the database\n"
        f"  bash serve.sh              — start MCP server (indexes + serves)",
        title="Ready",
    ))


# ---------------------------------------------------------------------------
# cap group
# ---------------------------------------------------------------------------

@cli.group()
def cap():
    """Manage capabilities."""
    pass


@cap.command("install")
@click.argument("cap_ids", nargs=-1, required=True)
def cap_install(cap_ids: tuple[str, ...]):
    """Install one or more capabilities (e.g. 001 002 006)."""
    if not _state.is_initialized():
        print_error("Not initialized. Run `ambientrag init` first.")
        raise click.Abort()

    state = _state.load_state()
    installed = _state.get_installed_caps()
    all_caps = registry.get_all_caps()

    # Normalize and validate
    normalized: list[str] = []
    for cid in cap_ids:
        nid = registry.normalize_cap_id(cid)
        if nid not in all_caps:
            print_error(f"Unknown cap: {cid} (normalized: {nid})")
            raise click.Abort()
        normalized.append(nid)

    # Check tier requirements early
    current_tier = state.get("tier", 1)
    for nid in normalized:
        if not registry.check_tier_requirement(nid, current_tier):
            cap_info = registry.get_cap(nid)
            min_tier = cap_info.get("tier_min", 0)
            tier_name = get_tier_info(min_tier).name if validate_tier(min_tier) else f"T{min_tier}"
            print_error(
                f"CAP-{nid} ({cap_info['name']}) requires tier {min_tier} ({tier_name}). "
                f"You're on T{current_tier}. Run `ambientrag upgrade {min_tier}` first."
            )
            raise click.Abort()

    # Resolve install order (includes uninstalled deps)
    order = registry.get_install_order(normalized, installed)

    if not order:
        console.print("[green]All requested capabilities are already installed.[/green]")
        return

    # Warn about deps being auto-installed
    explicit = set(normalized)
    deps_to_install = [cid for cid in order if cid not in explicit]
    if deps_to_install:
        dep_names = [f"CAP-{d} ({registry.get_cap(d)['name']})" for d in deps_to_install]
        print_info(f"Also installing dependencies: {', '.join(dep_names)}")
        if not click.confirm("Proceed?", default=True):
            return

    results: list[tuple[str, bool, str]] = []
    for cid in order:
        cap_info = registry.get_cap(cid)
        console.print(f"\n[bold]Installing CAP-{cid}: {cap_info['name']}[/bold]")
        try:
            mod = registry.get_cap_module(cid)
            ok = mod.install.install(state)
        except Exception as e:
            print_error(f"Install error: {e}")
            results.append((cid, False, str(e)))
            continue

        if ok:
            try:
                success, msg = mod.verify.verify(state)
            except Exception as e:
                success, msg = False, str(e)
            if success:
                version = cap_info.get("version_introduced") or "0.0.0"
                _state.mark_cap_installed(cid, version)
                results.append((cid, True, msg))
            else:
                print_warning(f"Install succeeded but verify failed: {msg}")
                results.append((cid, False, msg))
        else:
            results.append((cid, False, "Install returned False"))

    # Summary table
    console.print("")
    t = Table(title="Install Summary", show_header=True)
    t.add_column("CAP")
    t.add_column("Name")
    t.add_column("Result")
    t.add_column("Message")
    for cid, ok, msg in results:
        cap_info = registry.get_cap(cid)
        status = "[green]INSTALLED[/green]" if ok else "[red]FAILED[/red]"
        t.add_row(f"CAP-{cid}", cap_info["name"], status, msg)
    console.print(t)

    # Auto-refresh companion docs if any platforms are connected
    any_installed = any(ok for _, ok, _ in results)
    if any_installed:
        from ambientrag.companion import refresh_all_companions

        refreshed_state = _state.load_state()
        refreshed = refresh_all_companions(refreshed_state)
        if refreshed:
            print_success(f"Updated companion docs: {', '.join(refreshed)}")


@cap.command("list")
def cap_list():
    """List all capabilities and their install status."""
    all_caps = registry.get_all_caps()
    current_tier = _state.get_tier() if _state.is_initialized() else 1
    installed = _state.get_installed_caps() if _state.is_initialized() else {}

    # Check if any cap has enhances entries
    any_enhances = any(info.get("enhances") for info in all_caps.values())

    t = Table(title="AmbientRAG Capabilities", show_header=True)
    t.add_column("CAP", style="bold")
    t.add_column("Name")
    t.add_column("Status")
    t.add_column("Requires")
    if any_enhances:
        t.add_column("Enhances")
    t.add_column("Min Tier")

    for cap_id, info in all_caps.items():
        requires = ", ".join(info.get("requires") or []) or "\u2014"
        enhances = ", ".join(info.get("enhances") or []) or "\u2014"
        min_tier = info.get("tier_min", 0)
        tier_label = f"T{min_tier}"

        if cap_id in installed:
            cap_state = installed[cap_id]
            if cap_state.get("enabled", True):
                status = "[green]\u2713 installed[/green]"
            else:
                status = "[dim]\u2298 disabled[/dim]"
        elif current_tier < min_tier:
            status = f"[red]\u2717 need T{min_tier}[/red]"
        else:
            status = "[yellow]\u2014 available[/yellow]"

        row = [f"CAP-{cap_id}", info["name"], status, requires]
        if any_enhances:
            row.append(enhances)
        row.append(tier_label)
        t.add_row(*row)

    console.print(t)


@cap.command("uninstall")
@click.argument("cap_id")
@click.option("--cascade", is_flag=True, help="Also uninstall hard dependents")
@click.option("--force", is_flag=True, help="Required for CAP-001 (destroys all data)")
def cap_uninstall(cap_id: str, cascade: bool, force: bool):
    """Uninstall a capability (e.g. ambientrag cap uninstall 005)."""
    if not _state.is_initialized():
        print_error("Not initialized. Run `ambientrag init` first.")
        raise click.Abort()

    cap_id = registry.normalize_cap_id(cap_id)
    installed = _state.get_installed_caps()

    if cap_id not in installed:
        print_error(f"CAP-{cap_id} is not installed.")
        return

    # CAP-001 requires --force
    if cap_id == "001" and not force:
        print_error(
            "CAP-001 is the foundation — uninstalling drops ALL indexed data.\n"
            "  Use: ambientrag cap uninstall 001 --force"
        )
        return

    # Check reverse dependencies
    rev_deps = registry.get_reverse_dependencies(cap_id, installed)
    hard_deps = rev_deps["hard"]
    soft_deps = rev_deps["soft"]

    if hard_deps or soft_deps:
        # Show impact table
        cap_info = registry.get_cap(cap_id)
        console.print("")
        t = Table(title="Removal Impact Analysis", show_header=True)
        t.add_column("CAP", style="bold")
        t.add_column("Name")
        t.add_column("Type")
        t.add_column("Impact")

        for dep_id in hard_deps:
            dep_info = registry.get_cap(dep_id)
            dep_name = dep_info["name"] if dep_info else dep_id
            t.add_row(
                f"CAP-{dep_id}", dep_name,
                "[red]HARD[/red]", "Will be uninstalled"
            )
        for dep_id in soft_deps:
            dep_info = registry.get_cap(dep_id)
            dep_name = dep_info["name"] if dep_info else dep_id
            t.add_row(
                f"CAP-{dep_id}", dep_name,
                "[yellow]SOFT[/yellow]", f"Loses {cap_info['name']}, still works"
            )
        console.print(t)

    if hard_deps and not cascade:
        dep_names = ", ".join(f"CAP-{d}" for d in hard_deps)
        print_error(
            f"CAP-{cap_id} has hard dependents: {dep_names}\n"
            f"  Use: ambientrag cap uninstall {cap_id} --cascade"
        )

        # Load-bearing detection
        cap_data = installed[cap_id]
        installed_at = cap_data.get("installed_at")
        if installed_at:
            try:
                install_date = datetime.fromisoformat(installed_at)
                days_installed = (datetime.now(timezone.utc) - install_date).days
                total_rev = len(hard_deps) + len(soft_deps)
                if days_installed > 30 and total_rev >= 2:
                    print_warning(
                        f"CAP-{cap_id} is load-bearing "
                        f"(installed {days_installed} days, {total_rev} dependents).\n"
                        f"  Consider: ambientrag cap disable {cap_id}\n"
                        f"  This preserves schema while skipping at runtime."
                    )
            except (ValueError, TypeError):
                pass
        return

    # Build uninstall order
    state = _state.load_state()

    if cascade and hard_deps:
        removal_order = registry.get_uninstall_order([cap_id], installed)
    else:
        removal_order = [cap_id]

    # Confirm
    if len(removal_order) > 1:
        names = ", ".join(f"CAP-{c}" for c in removal_order)
        print_warning(f"Will uninstall in order: {names}")
        if not click.confirm("Proceed?", default=False):
            return

    results: list[tuple[str, bool, str]] = []
    for cid in removal_order:
        cap_info = registry.get_cap(cid)
        cap_name = cap_info["name"] if cap_info else cid
        console.print(f"\n[bold]Uninstalling CAP-{cid}: {cap_name}[/bold]")
        try:
            mod = registry.get_cap_module(cid)
            ok = mod.uninstall.uninstall(state)
        except Exception as e:
            print_error(f"Uninstall error: {e}")
            results.append((cid, False, str(e)))
            continue

        if ok:
            _state.mark_cap_uninstalled(cid)
            # Reload state for subsequent uninstalls
            state = _state.load_state()
            results.append((cid, True, "Removed"))
        else:
            results.append((cid, False, "Uninstall returned False"))

    # Summary
    console.print("")
    t = Table(title="Uninstall Summary", show_header=True)
    t.add_column("CAP")
    t.add_column("Name")
    t.add_column("Result")
    for cid, ok, msg in results:
        cap_info = registry.get_cap(cid)
        cap_name = cap_info["name"] if cap_info else cid
        status_str = "[green]REMOVED[/green]" if ok else "[red]FAILED[/red]"
        t.add_row(f"CAP-{cid}", cap_name, status_str)
    console.print(t)


@cap.command("disable")
@click.argument("cap_id")
def cap_disable(cap_id: str):
    """Disable a capability (preserves schema, skipped at runtime)."""
    if not _state.is_initialized():
        print_error("Not initialized. Run `ambientrag init` first.")
        raise click.Abort()

    cap_id = registry.normalize_cap_id(cap_id)
    installed = _state.get_installed_caps()

    if cap_id not in installed:
        print_error(f"CAP-{cap_id} is not installed.")
        return

    # Cannot disable CAP-001
    if cap_id == "001":
        print_error("CAP-001 (Foundation) cannot be disabled — nothing works without it.")
        return

    # Already disabled?
    if not installed[cap_id].get("enabled", True):
        print_warning(f"CAP-{cap_id} is already disabled.")
        return

    # Warn about dependents
    rev_deps = registry.get_reverse_dependencies(cap_id, installed)
    if rev_deps["hard"]:
        dep_names = ", ".join(f"CAP-{d}" for d in rev_deps["hard"])
        print_warning(f"Hard dependents will see this cap as inactive: {dep_names}")

    _state.mark_cap_disabled(cap_id)
    cap_info = registry.get_cap(cap_id)
    cap_name = cap_info["name"] if cap_info else cap_id
    print_success(f"CAP-{cap_id} ({cap_name}) disabled — schema preserved, skipped at runtime")


@cap.command("enable")
@click.argument("cap_id")
def cap_enable(cap_id: str):
    """Re-enable a disabled capability."""
    if not _state.is_initialized():
        print_error("Not initialized. Run `ambientrag init` first.")
        raise click.Abort()

    cap_id = registry.normalize_cap_id(cap_id)
    installed = _state.get_installed_caps()

    if cap_id not in installed:
        print_error(f"CAP-{cap_id} is not installed.")
        return

    if installed[cap_id].get("enabled", True):
        print_warning(f"CAP-{cap_id} is already enabled.")
        return

    _state.mark_cap_enabled(cap_id)
    cap_info = registry.get_cap(cap_id)
    cap_name = cap_info["name"] if cap_info else cap_id
    print_success(f"CAP-{cap_id} ({cap_name}) re-enabled")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@cli.command()
def status():
    """Show system status and verify installed capabilities."""
    if not _state.is_initialized():
        console.print("[yellow]Not initialized. Run `ambientrag init --vault-path /path/to/vault[/yellow]")
        return

    state = _state.load_state()
    tier = state.get("tier", 1)
    tier_info = get_tier_info(tier)
    vault_path = state.get("vault_path", "unknown")
    db_url = state.get("db_url")

    console.print(Panel(
        f"Vault:  [cyan]{vault_path}[/cyan]\n"
        f"Tier:   [yellow]T{tier} — {tier_info.name}[/yellow] ({tier_info.performance})\n"
        f"DB:     [dim]{db_url}[/dim]",
        title="AmbientRAG Status",
    ))

    # DB connection check
    if tier == 0:
        db_path = state.get("db_path")
        if db_path:
            from pathlib import Path as _P
            if _P(db_path).exists():
                print_success(f"SQLite database exists at {db_path}")
            else:
                print_warning(f"SQLite database not yet created at {db_path}")
        else:
            print_warning("No db_path configured for T0")
    elif db_url:
        try:
            import psycopg2
            conn = psycopg2.connect(db_url, connect_timeout=3)
            conn.close()
            print_success("Database connection OK")
        except Exception as e:
            print_error(f"Database connection failed: {e}")
    else:
        print_warning("No db_url configured")

    # Verify installed caps
    installed = _state.get_installed_caps()
    if installed:
        console.print("")
        t = Table(title="Installed Capabilities", show_header=True)
        t.add_column("CAP")
        t.add_column("Name")
        t.add_column("Verify")
        t.add_column("Message")

        for cap_id in sorted(installed.keys()):
            cap_info = registry.get_cap(cap_id)
            name = cap_info["name"] if cap_info else cap_id
            cap_data = installed[cap_id]
            enabled = cap_data.get("enabled", True)

            if not enabled:
                verify_str = "[dim]\u2298 disabled[/dim]"
                msg = "Schema preserved, skipped at runtime"
            else:
                try:
                    mod = registry.get_cap_module(cap_id)
                    ok, msg = mod.verify.verify(state)
                    verify_str = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
                except Exception as e:
                    verify_str = "[red]ERROR[/red]"
                    msg = str(e)
            t.add_row(f"CAP-{cap_id}", name, verify_str, msg)

        console.print(t)
    else:
        console.print("\n[dim]No capabilities installed yet.[/dim]")

    # Verify installed tools
    from ambientrag.tools import registry as tool_registry
    installed_tools = _state.get_installed_tools()
    if installed_tools:
        console.print("")
        t_tools = Table(title="Installed Tools", show_header=True)
        t_tools.add_column("TOOL")
        t_tools.add_column("Name")
        t_tools.add_column("Verify")
        t_tools.add_column("Message")

        for tool_id in sorted(installed_tools.keys()):
            tool_info = tool_registry.get_tool(tool_id)
            name = tool_info["name"] if tool_info else tool_id
            tool_data = installed_tools[tool_id]
            enabled = tool_data.get("enabled", True)

            if not enabled:
                verify_str = "[dim]\u2298 disabled[/dim]"
                msg = "Schema preserved, skipped at runtime"
            else:
                try:
                    mod = tool_registry.get_tool_module(tool_id)
                    ok, msg = mod.verify.verify(state)
                    verify_str = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
                except Exception as e:
                    verify_str = "[red]ERROR[/red]"
                    msg = str(e)
            t_tools.add_row(f"TOOL-{tool_id}", name, verify_str, msg)

        console.print(t_tools)

    # Port checks
    console.print("")
    ports = {8100: "MCP server", 8787: "Embed server"}
    if "005" in installed:
        ports[8788] = "Reranker"

    t2 = Table(title="Port Status", show_header=True)
    t2.add_column("Port")
    t2.add_column("Service")
    t2.add_column("Status")
    for port, svc in ports.items():
        up = check_port(port)
        t2.add_row(str(port), svc, "[green]UP[/green]" if up else "[dim]DOWN[/dim]")
    console.print(t2)


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("tier", type=int)
def upgrade(tier: int):
    """Upgrade infrastructure tier (e.g. `ambientrag upgrade 2`)."""
    if not _state.is_initialized():
        print_error("Not initialized. Run `ambientrag init` first.")
        raise click.Abort()

    if not validate_tier(tier):
        print_error(f"Invalid tier {tier}. Valid tiers: 0-3")
        raise click.Abort()

    current_tier = _state.get_tier()
    if not can_upgrade(current_tier, tier):
        if tier == current_tier:
            console.print(f"[yellow]Already on tier {tier}.[/yellow]")
        else:
            print_error(f"Cannot downgrade from T{current_tier} to T{tier}. Downgrades not supported.")
        return

    from_info = get_tier_info(current_tier)
    to_info = get_tier_info(tier)

    console.print(Panel(
        f"Upgrading T{current_tier} ({from_info.name}) → T{tier} ({to_info.name})\n\n"
        f"[dim]{to_info.description}[/dim]",
        title="Tier Upgrade",
    ))

    _state.set_tier(tier)
    print_success(f"Tier updated to T{tier} ({to_info.name})")
    print_warning("TODO: Data migration for tier upgrade not yet implemented.")
    print_info("Re-run `ambientrag cap install` for capabilities that require the new tier.")


# ---------------------------------------------------------------------------
# tool group
# ---------------------------------------------------------------------------

@cli.group()
def tool():
    """Manage optional tools (metrics, monitoring)."""
    pass


@tool.command("install")
@click.argument("tool_ids", nargs=-1, required=True)
def tool_install(tool_ids: tuple[str, ...]):
    """Install one or more tools (e.g. 001 002)."""
    if not _state.is_initialized():
        print_error("Not initialized. Run `ambientrag init` first.")
        raise click.Abort()

    from ambientrag.tools import registry as tool_registry

    state = _state.load_state()
    installed_tools = _state.get_installed_tools()
    all_tools = tool_registry.get_all_tools()

    results: list[tuple[str, bool, str]] = []
    for tid in tool_ids:
        nid = tool_registry.normalize_tool_id(tid)
        if nid not in all_tools:
            print_error(f"Unknown tool: {tid} (normalized: {nid})")
            raise click.Abort()

        if nid in installed_tools:
            console.print(f"[green]TOOL-{nid} already installed.[/green]")
            continue

        tool_info = all_tools[nid]
        console.print(f"\n[bold]Installing TOOL-{nid}: {tool_info['name']}[/bold]")
        try:
            mod = tool_registry.get_tool_module(nid)
            ok = mod.install.install(state)
        except Exception as e:
            print_error(f"Install error: {e}")
            results.append((nid, False, str(e)))
            continue

        if ok:
            try:
                success, msg = mod.verify.verify(state)
            except Exception as e:
                success, msg = False, str(e)
            if success:
                _state.mark_tool_installed(nid, "0.1.0")
                results.append((nid, True, msg))
            else:
                print_warning(f"Install succeeded but verify failed: {msg}")
                results.append((nid, False, msg))
        else:
            results.append((nid, False, "Install returned False"))

    if results:
        console.print("")
        t = Table(title="Tool Install Summary", show_header=True)
        t.add_column("TOOL")
        t.add_column("Name")
        t.add_column("Result")
        t.add_column("Message")
        for nid, ok, msg in results:
            tool_info = all_tools.get(nid, {})
            status = "[green]INSTALLED[/green]" if ok else "[red]FAILED[/red]"
            t.add_row(f"TOOL-{nid}", tool_info.get("name", nid), status, msg)
        console.print(t)


@tool.command("uninstall")
@click.argument("tool_id")
def tool_uninstall(tool_id: str):
    """Uninstall a tool."""
    if not _state.is_initialized():
        print_error("Not initialized. Run `ambientrag init` first.")
        raise click.Abort()

    from ambientrag.tools import registry as tool_registry

    tool_id = tool_registry.normalize_tool_id(tool_id)
    installed_tools = _state.get_installed_tools()

    if tool_id not in installed_tools:
        print_error(f"TOOL-{tool_id} is not installed.")
        return

    state = _state.load_state()
    tool_info = tool_registry.get_tool(tool_id)
    tool_name = tool_info["name"] if tool_info else tool_id

    console.print(f"\n[bold]Uninstalling TOOL-{tool_id}: {tool_name}[/bold]")
    try:
        mod = tool_registry.get_tool_module(tool_id)
        ok = mod.uninstall.uninstall(state)
    except Exception as e:
        print_error(f"Uninstall error: {e}")
        return

    if ok:
        _state.mark_tool_uninstalled(tool_id)
        print_success(f"TOOL-{tool_id} ({tool_name}) uninstalled")
    else:
        print_error(f"TOOL-{tool_id} uninstall failed")


@tool.command("list")
def tool_list():
    """List all tools and their install status."""
    from ambientrag.tools import registry as tool_registry

    all_tools = tool_registry.get_all_tools()
    installed_tools = _state.get_installed_tools() if _state.is_initialized() else {}

    t = Table(title="AmbientRAG Tools", show_header=True)
    t.add_column("TOOL", style="bold")
    t.add_column("Name")
    t.add_column("Description")
    t.add_column("Status")

    for tool_id, info in all_tools.items():
        if tool_id in installed_tools:
            tool_state = installed_tools[tool_id]
            if tool_state.get("enabled", True):
                status = "[green]\u2713 installed[/green]"
            else:
                status = "[dim]\u2298 disabled[/dim]"
        else:
            status = "[yellow]\u2014 available[/yellow]"

        t.add_row(f"TOOL-{tool_id}", info["name"], info.get("description", ""), status)

    console.print(t)


@tool.command("disable")
@click.argument("tool_id")
def tool_disable(tool_id: str):
    """Disable a tool (preserves schema, skipped at runtime)."""
    if not _state.is_initialized():
        print_error("Not initialized. Run `ambientrag init` first.")
        raise click.Abort()

    from ambientrag.tools import registry as tool_registry

    tool_id = tool_registry.normalize_tool_id(tool_id)
    installed_tools = _state.get_installed_tools()

    if tool_id not in installed_tools:
        print_error(f"TOOL-{tool_id} is not installed.")
        return

    if not installed_tools[tool_id].get("enabled", True):
        print_warning(f"TOOL-{tool_id} is already disabled.")
        return

    _state.mark_tool_disabled(tool_id)
    tool_info = tool_registry.get_tool(tool_id)
    tool_name = tool_info["name"] if tool_info else tool_id
    print_success(f"TOOL-{tool_id} ({tool_name}) disabled")


@tool.command("enable")
@click.argument("tool_id")
def tool_enable(tool_id: str):
    """Re-enable a disabled tool."""
    if not _state.is_initialized():
        print_error("Not initialized. Run `ambientrag init` first.")
        raise click.Abort()

    from ambientrag.tools import registry as tool_registry

    tool_id = tool_registry.normalize_tool_id(tool_id)
    installed_tools = _state.get_installed_tools()

    if tool_id not in installed_tools:
        print_error(f"TOOL-{tool_id} is not installed.")
        return

    if installed_tools[tool_id].get("enabled", True):
        print_warning(f"TOOL-{tool_id} is already enabled.")
        return

    _state.mark_tool_enabled(tool_id)
    tool_info = tool_registry.get_tool(tool_id)
    tool_name = tool_info["name"] if tool_info else tool_id
    print_success(f"TOOL-{tool_id} ({tool_name}) re-enabled")


# ---------------------------------------------------------------------------
# Register sub-command groups from other modules
# ---------------------------------------------------------------------------

from ambientrag.bench import bench  # noqa: E402
from ambientrag.demo import demo  # noqa: E402
from ambientrag.connect import connect  # noqa: E402
from ambientrag.diagram import diagram  # noqa: E402
from ambientrag.doctor import doctor  # noqa: E402
from ambientrag.intake import intake  # noqa: E402
from ambientrag.migrate import migrate  # noqa: E402
from ambientrag.search import search  # noqa: E402

cli.add_command(bench)
cli.add_command(demo)
cli.add_command(connect)
cli.add_command(diagram)
cli.add_command(doctor)
cli.add_command(intake)
cli.add_command(migrate)
cli.add_command(search)


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--vault-path", type=click.Path(), default=None, help="Path to Obsidian vault (default: initialized vault)")
@click.option("--force", is_flag=True, help="Re-index all files (ignore content hash)")
def index(vault_path: str | None, force: bool):
    """Index vault markdown files into the search database.

    Reads .md files, chunks by heading, and inserts into vault_chunks.
    Only re-indexes files that have changed (unless --force).

    \b
    Examples:
      ambientrag index                    # index changed files
      ambientrag index --force            # re-index everything
      ambientrag index --vault-path ~/ambientrag
    """
    import time as _time

    if not _state.is_initialized():
        print_error("Not initialized. Run `ambientrag init` first.")
        raise click.Abort()

    state = _state.load_state()
    vp = vault_path or state.get("vault_path", ".")

    print_info(f"Indexing {vp}...")

    start = _time.perf_counter()

    from ambientrag.indexer import index_vault
    stats = index_vault(state, vault_path=vault_path, force=force)

    elapsed = _time.perf_counter() - start

    total_files = stats["files_scanned"]
    inserted = stats["chunks_inserted"]
    updated = stats["chunks_updated"]
    unchanged = stats["files_unchanged"]
    deleted = stats["chunks_deleted"]

    print_success(
        f"Indexed {total_files} files ({inserted} chunks) in {elapsed:.1f}s"
    )
    console.print(
        f"     New: {inserted} | Updated: {updated} | "
        f"Unchanged: {unchanged} | Deleted: {deleted}"
    )


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--host", default="127.0.0.1", help="Host to bind (use 0.0.0.0 for remote/Tailscale)")
@click.option("--port", default=8100, type=int, help="Port number")
@click.option("--transport", default="streamable-http", type=click.Choice(["streamable-http", "sse"]),
              help="MCP transport (default: streamable-http)")
def serve(host: str, port: int, transport: str):
    """Start the AmbientRAG MCP server.

    This is the endpoint that AI assistants connect to.
    Supports Streamable HTTP (default) and SSE (legacy) transports.

    \b
    Examples:
      ambientrag serve                    # streamable HTTP, port 8100
      ambientrag serve --transport sse    # legacy SSE transport
      ambientrag serve --host 0.0.0.0     # allow remote (Tailscale)
      ambientrag serve --port 9100        # custom port

    \b
    Endpoints:
      http://HOST:PORT/mcp               — MCP Streamable HTTP (for AI clients)
      http://HOST:PORT/health            — health check (JSON)
      http://HOST:PORT/api/search?q=...  — REST search (JSON)

    \b
    Test with curl:
      curl http://localhost:8100/health
      curl http://localhost:8100/api/search?q=hello
    """
    if not _state.is_initialized():
        print_error("Not initialized. Run `ambientrag init` first.")
        raise click.Abort()

    state = _state.load_state()
    vault_path = state.get("vault_path", "unknown")
    tier = state.get("tier", 0)
    tier_info = get_tier_info(tier)

    # Count chunks
    chunk_count = 0
    try:
        from ambientrag.db import get_backend
        backend = get_backend(state)
        backend.connect()
        chunk_count = backend.count_rows("vault_chunks")
        backend.close()
    except Exception:
        pass

    installed_caps = sorted(_state.get_installed_caps().keys())
    installed_tools = sorted(_state.get_installed_tools().keys())

    caps_str = ", ".join(installed_caps) if installed_caps else "none"
    tools_str = ", ".join(installed_tools) if installed_tools else "none"
    backend_label = f"SQLite + sqlite-vec" if tier == 0 else f"PostgreSQL ({tier_info.name})"

    endpoint_path = "/mcp" if transport == "streamable-http" else "/sse"
    console.print(Panel(
        f"Vault:    [cyan]{vault_path}[/cyan]\n"
        f"Backend:  [yellow]{backend_label}[/yellow]\n"
        f"Transport:[bold] {transport}[/bold]\n"
        f"Endpoint: [bold]http://{host}:{port}{endpoint_path}[/bold]\n"
        f"Chunks:   {chunk_count:,}\n"
        f"CAPs:     {caps_str}\n"
        f"Tools:    {tools_str}",
        title="AmbientRAG MCP Server",
    ))

    # Start intake watcher alongside MCP server
    watcher = None
    try:
        from ambientrag.watcher import IntakeWatcher

        watcher = IntakeWatcher(vault_path)
        inbox_dirs = watcher.pipeline.get_all_inbox_dirs()
        if inbox_dirs:
            watcher.start()
            print_success(f"Intake watcher started ({len(inbox_dirs)} inbox dirs)")
        else:
            print_warning("No inbox directories found — intake watcher not started")
    except Exception as e:
        print_warning(f"Intake watcher failed to start: {e}")

    print_info(f"MCP server starting. Press Ctrl+C to stop.")
    print_info(f"Test: curl http://{host}:{port}/health")

    try:
        from ambientrag.server import start_server
        start_server(host=host, port=port, transport=transport)
    finally:
        if watcher and watcher.is_running:
            watcher.stop()
            print_info("Intake watcher stopped")
