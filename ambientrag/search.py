"""AmbientRAG search — query the vault directly from the CLI."""
from __future__ import annotations

import time

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import ambientrag.state as _state
from ambientrag.db import get_backend
from ambientrag.utils import print_error, print_info

console = Console()


def _search_fts(backend, query: str, limit: int = 5) -> list[dict]:
    """Search using FTS5 (SQLite) or tsvector (Postgres)."""
    tier = _state.get_tier() if _state.is_initialized() else 0

    if tier == 0:
        # SQLite — try FTS5 first, fall back to LIKE
        try:
            rows = backend.fetchall(
                "SELECT vc.id, vc.source_path, vc.chunk_heading, "
                "snippet(vault_chunks_fts, 0, '**', '**', '...', 32) as snippet "
                "FROM vault_chunks_fts fts "
                "JOIN vault_chunks vc ON vc.id = fts.rowid "
                "WHERE vault_chunks_fts MATCH ? "
                "LIMIT ?",
                (query, limit),
            )
            return [{"id": r[0], "path": r[1], "heading": r[2], "snippet": r[3]} for r in rows]
        except Exception:
            # FTS5 not populated — fall back to LIKE
            rows = backend.fetchall(
                "SELECT id, source_path, chunk_heading, "
                "substr(chunk_text, 1, 200) as snippet "
                "FROM vault_chunks "
                "WHERE chunk_text LIKE ? "
                "LIMIT ?",
                (f"%{query}%", limit),
            )
            return [{"id": r[0], "path": r[1], "heading": r[2], "snippet": r[3]} for r in rows]
    else:
        # Postgres — use tsvector
        try:
            rows = backend.fetchall(
                "SELECT id, source_path, chunk_heading, "
                "ts_headline('english', chunk_text, plainto_tsquery('english', %s), "
                "'StartSel=**, StopSel=**, MaxFragments=1, MaxWords=30') as snippet "
                "FROM vault_chunks "
                "WHERE tsv @@ plainto_tsquery('english', %s) "
                "ORDER BY ts_rank(tsv, plainto_tsquery('english', %s)) DESC "
                "LIMIT %s",
                (query, query, query, limit),
            )
            return [{"id": r[0], "path": r[1], "heading": r[2], "snippet": r[3]} for r in rows]
        except Exception:
            # Fall back to LIKE
            rows = backend.fetchall(
                "SELECT id, source_path, chunk_heading, "
                "substring(chunk_text from 1 for 200) as snippet "
                "FROM vault_chunks "
                "WHERE chunk_text ILIKE %s "
                "LIMIT %s",
                (f"%{query}%", limit),
            )
            return [{"id": r[0], "path": r[1], "heading": r[2], "snippet": r[3]} for r in rows]


@click.command()
@click.argument("query")
@click.option("--limit", default=5, help="Max results to return")
def search(query: str, limit: int):
    """Search the vault directly from the CLI.

    No MCP server or AI client needed — queries the database and shows results.

    \b
    Examples:
      ambientrag search "hello world"
      ambientrag search "how does search work"
      ambientrag search "coffee" --limit 3
    """
    if not _state.is_initialized():
        print_error("Not initialized. Run `ambientrag init` first.")
        raise click.Abort()

    state = _state.load_state()
    backend = get_backend(state)

    try:
        backend.connect()

        chunk_count = 0
        try:
            chunk_count = backend.count_rows("vault_chunks")
        except Exception:
            pass

        if chunk_count == 0:
            console.print(Panel(
                f'[bold]Query:[/bold] "{query}"\n'
                f"[bold]Chunks:[/bold] 0\n\n"
                f"[yellow]Vault is empty — no chunks indexed yet.[/yellow]\n\n"
                f"If you just ran [bold]ambientrag demo seed[/bold], the demo notes\n"
                f"are in your vault but haven't been indexed. The indexer\n"
                f"(index_vault.py + MCP server) processes them into searchable chunks.\n\n"
                f"For now, you can verify the demo notes exist:\n"
                f"  ambientrag demo status",
                title="Search",
            ))
            return

        start = time.perf_counter()
        results = _search_fts(backend, query, limit)
        elapsed = (time.perf_counter() - start) * 1000

        console.print(Panel(
            f'[bold]Query:[/bold] "{query}"\n'
            f"[bold]Results:[/bold] {len(results)} (searched {chunk_count:,} chunks in {elapsed:.0f}ms)",
            title="Search",
        ))

        if not results:
            console.print(f'\n[dim]No results for "{query}". Try different terms.[/dim]')
            return

        t = Table(show_header=True, expand=True)
        t.add_column("#", width=3)
        t.add_column("Source", style="cyan", ratio=1)
        t.add_column("Heading", ratio=1)
        t.add_column("Snippet", ratio=2)

        for i, r in enumerate(results, 1):
            path = r["path"] or ""
            # Shorten path — show just the filename
            short_path = path.split("/")[-1] if "/" in path else path
            heading = r["heading"] or ""
            snippet = (r["snippet"] or "")[:150]
            t.add_row(str(i), short_path, heading, snippet)

        console.print(t)

    except Exception as e:
        print_error(f"Search failed: {e}")
        print_info("Try: ambientrag doctor")
    finally:
        backend.close()
