"""AmbientRAG connect command group — set up integrations with AI clients."""
from __future__ import annotations

import re
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

import ambientrag.state as _state
from ambientrag.companion import refresh_all_companions, write_companion
from ambientrag.utils import print_info, print_success, print_warning

console = Console()

DEFAULT_MCP_PORT = 8100
DEFAULT_HOST = "127.0.0.1"

INTEGRATIONS = {
    "codex": "OpenAI Codex CLI — writes ~/.codex/config.toml + companion doc",
    "claude-code": "Anthropic Claude Code — prints the `claude mcp add` command + companion doc",
}


def _mcp_url(host: str | None = None) -> str:
    """Build the MCP Streamable HTTP endpoint URL."""
    h = host or DEFAULT_HOST
    return f"http://{h}:{DEFAULT_MCP_PORT}/mcp"


def _update_codex_config(url: str) -> Path:
    """Write or update ~/.codex/config.toml with the MCP server entry.

    Uses simple string manipulation (no TOML library) for Python 3.9 compat.
    Preserves existing config content outside the [mcp_servers.ambientrag] section.
    """
    codex_dir = Path.home() / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    config_path = codex_dir / "config.toml"

    new_section = f'[mcp_servers.ambientrag]\nurl = "{url}"\n'

    if config_path.exists():
        content = config_path.read_text(encoding="utf-8")

        # Pattern: match [mcp_servers.ambientrag] section until next [section] or EOF
        pattern = r"\[mcp_servers\.ambientrag\]\s*\n(?:[^\[]*?)(?=\n\[|\Z)"
        if re.search(pattern, content):
            # Replace existing section
            content = re.sub(pattern, new_section.rstrip(), content)
        else:
            # Append new section
            if not content.endswith("\n"):
                content += "\n"
            content += "\n" + new_section
    else:
        content = new_section

    config_path.write_text(content, encoding="utf-8")
    return config_path


def _write_companion_for_platform(platform: str) -> Path | None:
    """Generate and write the companion doc for a platform.

    Returns the path to the companion doc, or None if not initialized.
    """
    if not _state.is_initialized():
        return None

    state = _state.load_state()
    vault_path = state.get("vault_path")
    if not vault_path:
        return None

    doc_path = write_companion(platform, state, vault_path)
    _state.mark_platform_connected(platform, str(doc_path))
    return doc_path


# ---------------------------------------------------------------------------
# Click command group
# ---------------------------------------------------------------------------

@click.group(invoke_without_command=True)
@click.option("--list", "list_integrations", is_flag=True, help="Show available integrations")
@click.pass_context
def connect(ctx: click.Context, list_integrations: bool):
    """Set up integrations with AI clients (Codex, Claude Code)."""
    if list_integrations:
        t = Table(title="Available Integrations", show_header=True)
        t.add_column("Name", style="bold")
        t.add_column("Description")
        for name, desc in INTEGRATIONS.items():
            t.add_row(name, desc)
        console.print(t)
        return

    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


@connect.command()
@click.option("--remote", default=None, help="Remote hostname (e.g. gibson.tail1234.ts.net)")
def codex(remote: str | None):
    """Configure Codex CLI to use the AmbientRAG MCP server."""
    url = _mcp_url(remote)
    config_path = _update_codex_config(url)

    print_success(f"Wrote Codex config to {config_path}")
    console.print(f"  MCP endpoint: [cyan]{url}[/cyan]")

    if remote:
        console.print("")
        print_warning(
            "Make sure the MCP server binds 0.0.0.0 (not 127.0.0.1) for remote access"
        )

    # Generate companion doc
    doc_path = _write_companion_for_platform("codex")
    if doc_path:
        print_success(f"Wrote companion doc to {doc_path}")
    else:
        print_warning("Not initialized — skipping companion doc (run `ambientrag init` first)")

    console.print("")
    print_info('Test it: [bold]codex "search the vault for AmbientRAG"[/bold]')


@connect.command("claude-code")
@click.option("--remote", default=None, help="Remote hostname (e.g. gibson.tail1234.ts.net)")
def claude_code(remote: str | None):
    """Show the command to connect Claude Code to the AmbientRAG MCP server."""
    url = _mcp_url(remote)

    console.print("[bold]Run this command to add the MCP server to Claude Code:[/bold]")
    console.print("")
    console.print(f"  claude mcp add -s user -t http ambientrag {url}")
    console.print("")

    if remote:
        print_warning(
            "Make sure the MCP server binds 0.0.0.0 (not 127.0.0.1) for remote access"
        )
        console.print("")

    # Generate companion doc
    doc_path = _write_companion_for_platform("claude-code")
    if doc_path:
        print_success(f"Wrote companion doc to {doc_path}")
    else:
        print_warning("Not initialized — skipping companion doc (run `ambientrag init` first)")

    print_info("After adding, Claude Code will have access to your vault search tools.")


@connect.command("refresh")
def refresh():
    """Regenerate companion docs for all connected platforms."""
    if not _state.is_initialized():
        from ambientrag.utils import print_error
        print_error("Not initialized. Run `ambientrag init` first.")
        raise click.Abort()

    state = _state.load_state()
    refreshed = refresh_all_companions(state)

    if refreshed:
        for platform in refreshed:
            print_success(f"Updated companion doc: {platform}")
    else:
        print_warning("No platforms connected yet. Run `ambientrag connect codex` or `ambientrag connect claude-code` first.")
