"""AmbientRAG MCP Server — the interface between your vault and AI clients."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

import ambientrag.state as _state
from ambientrag.db import get_backend
from ambientrag.search import _search_fts


_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AmbientRAG — Token Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff;
    --green: #3fb950; --orange: #d29922; --red: #f85149;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg); color: var(--text);
    font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
    font-size: 14px; line-height: 1.5; padding: 24px;
  }
  h1 { font-size: 20px; color: var(--accent); margin-bottom: 4px; }
  .subtitle { color: var(--muted); font-size: 12px; margin-bottom: 24px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px;
  }
  .card h2 { font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }
  .stat { font-size: 32px; font-weight: bold; color: var(--green); }
  .stat-label { font-size: 12px; color: var(--muted); }
  .stats-row { display: flex; gap: 24px; flex-wrap: wrap; }
  .stats-row .stat-block { text-align: center; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: var(--muted); font-weight: normal; padding: 6px 8px; border-bottom: 1px solid var(--border); }
  td { padding: 6px 8px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  td.wrap { white-space: normal; word-break: break-all; }
  tr:hover td { background: rgba(88,166,255,0.05); }
  .token-count { color: var(--orange); font-variant-numeric: tabular-nums; }
  .tool-badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    background: rgba(88,166,255,0.1); color: var(--accent); font-size: 12px;
  }
  .session-id { color: var(--muted); font-size: 11px; max-width: 120px; overflow: hidden; text-overflow: ellipsis; }
  .timestamp { color: var(--muted); font-size: 12px; }
  .empty { color: var(--muted); text-align: center; padding: 40px; }
  .empty .icon { font-size: 48px; margin-bottom: 12px; }
  .chart-container { max-width: 300px; margin: 0 auto; }
  .refresh-note { color: var(--muted); font-size: 11px; text-align: right; margin-bottom: 8px; }
  .pulse { display: inline-block; width: 8px; height: 8px; background: var(--green); border-radius: 50%; margin-right: 6px; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
  .wide { grid-column: 1 / -1; }
  @media (max-width: 600px) { body { padding: 12px; } .grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<h1>AmbientRAG Token Dashboard</h1>
<p class="subtitle"><span class="pulse"></span>Live — refreshes every 30s | <a href="/health" style="color:var(--accent);">/health</a></p>

<div id="app">
  <div class="empty"><div class="icon">&#x23F3;</div>Loading...</div>
</div>

<script>
const BASE = window.location.origin;
let chart = null;

function fmt(n) { return n == null ? '—' : n.toLocaleString(); }

function timeAgo(ts) {
  if (!ts) return '—';
  const d = new Date(ts.includes('T') || ts.includes('+') ? ts : ts + 'Z');
  const s = Math.floor((Date.now() - d.getTime()) / 1000);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  if (s < 86400) return Math.floor(s/3600) + 'h ago';
  return Math.floor(s/86400) + 'd ago';
}

function shortSession(sid) {
  if (!sid) return '—';
  return sid.length > 12 ? sid.slice(0, 6) + '...' + sid.slice(-4) : sid;
}

async function load() {
  try {
    const [recentRes, summaryRes] = await Promise.all([
      fetch(BASE + '/api/tokens/recent?limit=50'),
      fetch(BASE + '/api/tokens/summary'),
    ]);

    if (!recentRes.ok || !summaryRes.ok) {
      const err = await recentRes.json().catch(() => ({}));
      document.getElementById('app').innerHTML =
        '<div class="empty"><div class="icon">&#x26A0;</div>' +
        (err.error || 'Token Hygiene (TOOL-002) not installed.') +
        '<br><br>Install it: <code>ambientrag install tool-002</code></div>';
      return;
    }

    const recent = await recentRes.json();
    const summary = await summaryRes.json();

    if (summary.today.calls === 0 && summary.all_time.calls === 0) {
      document.getElementById('app').innerHTML =
        '<div class="empty"><div class="icon">&#x1F4E1;</div>' +
        'No activity yet.<br><br>' +
        'Use your vault through an AI client and token usage will appear here.</div>';
      return;
    }

    render(recent, summary);
  } catch (e) {
    document.getElementById('app').innerHTML =
      '<div class="empty"><div class="icon">&#x274C;</div>Failed to load data: ' + e.message + '</div>';
  }
}

function render(recent, summary) {
  const t = summary.today;
  const a = summary.all_time;
  const tools = summary.tool_breakdown;
  const sessions = summary.sessions;
  const calls = recent.calls;

  let html = '<p class="refresh-note">Last refresh: ' + new Date().toLocaleTimeString() + '</p>';

  // --- Stats cards ---
  html += '<div class="grid">';
  html += '<div class="card"><h2>Today</h2><div class="stats-row">';
  html += '<div class="stat-block"><div class="stat">' + fmt(t.tokens) + '</div><div class="stat-label">tokens</div></div>';
  html += '<div class="stat-block"><div class="stat">' + fmt(t.calls) + '</div><div class="stat-label">calls</div></div>';
  html += '<div class="stat-block"><div class="stat">' + fmt(t.sessions) + '</div><div class="stat-label">sessions</div></div>';
  html += '</div></div>';

  html += '<div class="card"><h2>All Time</h2><div class="stats-row">';
  html += '<div class="stat-block"><div class="stat">' + fmt(a.tokens) + '</div><div class="stat-label">tokens</div></div>';
  html += '<div class="stat-block"><div class="stat">' + fmt(a.calls) + '</div><div class="stat-label">calls</div></div>';
  html += '</div></div>';

  // --- Chart ---
  html += '<div class="card"><h2>Tool Breakdown (Today)</h2>';
  if (tools.length === 0) {
    html += '<div class="empty">No tool data today</div>';
  } else {
    html += '<div class="chart-container"><canvas id="toolChart"></canvas></div>';
  }
  html += '</div>';
  html += '</div>';

  // --- Session table ---
  html += '<div class="grid"><div class="card wide"><h2>Sessions (Today)</h2>';
  if (sessions.length === 0) {
    html += '<div class="empty">No sessions today</div>';
  } else {
    html += '<table><tr><th>Session</th><th>Label</th><th>Tokens</th><th>Calls</th><th>First</th><th>Last</th></tr>';
    sessions.forEach(s => {
      html += '<tr>';
      html += '<td class="session-id" title="' + s.session_id + '">' + shortSession(s.session_id) + '</td>';
      html += '<td>' + (s.label || '<span style="color:var(--muted)">—</span>') + '</td>';
      html += '<td class="token-count">' + fmt(s.tokens) + '</td>';
      html += '<td>' + s.calls + '</td>';
      html += '<td class="timestamp">' + timeAgo(s.first_call) + '</td>';
      html += '<td class="timestamp">' + timeAgo(s.last_call) + '</td>';
      html += '</tr>';
    });
    html += '</table>';
  }
  html += '</div></div>';

  // --- Recent activity ---
  html += '<div class="grid"><div class="card wide"><h2>Recent Activity</h2>';
  if (calls.length === 0) {
    html += '<div class="empty">No recent calls</div>';
  } else {
    html += '<table><tr><th>Time</th><th>Tool</th><th>Tokens</th><th>Session</th></tr>';
    calls.forEach(c => {
      html += '<tr>';
      html += '<td class="timestamp">' + timeAgo(c.called_at) + '</td>';
      html += '<td><span class="tool-badge">' + c.tool_name + '</span></td>';
      html += '<td class="token-count">' + fmt(c.tokens_estimated) + '</td>';
      html += '<td class="session-id" title="' + c.session_id + '">' + shortSession(c.session_id) + '</td>';
      html += '</tr>';
    });
    html += '</table>';
  }
  html += '</div></div>';

  document.getElementById('app').innerHTML = html;

  // --- Draw chart ---
  if (tools.length > 0) {
    const ctx = document.getElementById('toolChart');
    if (ctx) {
      const colors = ['#58a6ff','#3fb950','#d29922','#f85149','#bc8cff','#79c0ff','#d2a8ff','#ffa657'];
      if (chart) chart.destroy();
      chart = new Chart(ctx, {
        type: 'doughnut',
        data: {
          labels: tools.map(t => t.tool),
          datasets: [{
            data: tools.map(t => t.tokens),
            backgroundColor: tools.map((_, i) => colors[i % colors.length]),
            borderWidth: 0,
          }],
        },
        options: {
          responsive: true,
          plugins: {
            legend: { position: 'bottom', labels: { color: '#8b949e', font: { size: 11 } } },
            tooltip: {
              callbacks: {
                label: (ctx) => ctx.label + ': ' + ctx.parsed.toLocaleString() + ' tokens'
              }
            }
          }
        }
      });
    }
  }
}

// Initial load + auto-refresh
load();
setInterval(load, 30000);
</script>
</body>
</html>
"""


def _read_note(vault_path: str, file_path: str) -> str:
    """Read a markdown file from the vault, with path traversal protection."""
    vault = Path(vault_path).resolve()
    target = (vault / file_path).resolve()

    # Security: ensure the resolved path is within the vault
    if not str(target).startswith(str(vault)):
        raise ValueError(f"Path traversal blocked: {file_path}")

    if not target.exists():
        raise FileNotFoundError(f"Note not found: {file_path}")

    if not target.is_file():
        raise ValueError(f"Not a file: {file_path}")

    return target.read_text(encoding="utf-8")


def _format_search_results(results: list[dict], query: str, chunk_count: int, elapsed_ms: float) -> str:
    """Format search results as human-readable text (LLMs prefer this over JSON)."""
    if not results:
        return f'No results for "{query}" (searched {chunk_count:,} chunks in {elapsed_ms:.0f}ms).'

    lines = [f'Found {len(results)} results for "{query}" (searched {chunk_count:,} chunks in {elapsed_ms:.0f}ms):\n']

    for i, r in enumerate(results, 1):
        path = r.get("path", "")
        heading = r.get("heading", "")
        snippet = r.get("snippet", "")

        header = f"{i}. {path}"
        if heading:
            header += f" — {heading}"
        lines.append(header)
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")

    return "\n".join(lines).rstrip()


def _log_search_metric(backend: Any, query: str, elapsed_ms: float, result_count: int) -> None:
    """Log search latency to search_metrics if TOOL-001 is installed."""
    if not _state.is_tool_active("001"):
        return
    try:
        tier = _state.get_tier()
        if tier == 0:
            backend.execute(
                "INSERT INTO search_metrics (query, latency_ms, result_count, search_type, created_at) "
                "VALUES (?, ?, ?, 'fts', datetime('now'))",
                (query, elapsed_ms, result_count),
            )
        else:
            backend.execute(
                "INSERT INTO search_metrics (query, latency_ms, result_count, search_type, created_at) "
                "VALUES (%s, %s, %s, 'fts', NOW())",
                (query, elapsed_ms, result_count),
            )
    except Exception:
        # Metrics logging should never break search
        pass


def create_server(state: dict, host: str = "127.0.0.1", port: int = 8100) -> FastMCP:
    """Create and configure the AmbientRAG MCP server.

    Returns a FastMCP instance with tools registered and REST endpoints added.
    """
    vault_path = state.get("vault_path", ".")

    mcp = FastMCP(
        "AmbientRAG",
        host=host,
        port=port,
        instructions=(
            "AmbientRAG provides search over an Obsidian vault. "
            "Use search_vault to find information, get_note to read full notes, "
            "and list_notes to browse indexed content."
        ),
    )

    # ------------------------------------------------------------------
    # MCP Tools
    # ------------------------------------------------------------------

    @mcp.tool()
    def search_vault(query: str, limit: int = 5) -> str:
        """Search the vault using keyword matching.

        Returns matching chunks with source path, heading, and snippet.
        Use this to find information in the vault before reading full notes.
        """
        backend = get_backend(state)
        try:
            backend.connect()

            chunk_count = 0
            try:
                chunk_count = backend.count_rows("vault_chunks")
            except Exception:
                pass

            if chunk_count == 0:
                return "Vault is empty — no chunks indexed yet. Run the indexer first."

            start = time.perf_counter()
            results = _search_fts(backend, query, limit)
            elapsed_ms = (time.perf_counter() - start) * 1000

            _log_search_metric(backend, query, elapsed_ms, len(results))

            return _format_search_results(results, query, chunk_count, elapsed_ms)
        except Exception as e:
            return f"Search error: {e}"
        finally:
            backend.close()

    @mcp.tool()
    def get_note(file_path: str) -> str:
        """Read a full note from the vault by file path.

        The file_path should be relative to the vault root
        (e.g. '_demo/hello-world.md' or 'projects/my-project/overview.md').
        """
        try:
            content = _read_note(vault_path, file_path)
            return content
        except FileNotFoundError as e:
            return f"Not found: {e}"
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading note: {e}"

    @mcp.tool()
    def list_notes(folder: str = "") -> str:
        """List indexed notes, optionally filtered by folder prefix.

        Returns a list of source paths that have been indexed into the vault.
        Use folder='' for all notes, or folder='_demo' for a specific folder.
        """
        backend = get_backend(state)
        try:
            backend.connect()
            tier = state.get("tier", 0)

            if folder:
                if tier == 0:
                    rows = backend.fetchall(
                        "SELECT DISTINCT source_path FROM vault_chunks "
                        "WHERE source_path LIKE ? ORDER BY source_path",
                        (f"{folder}%",),
                    )
                else:
                    rows = backend.fetchall(
                        "SELECT DISTINCT source_path FROM vault_chunks "
                        "WHERE source_path LIKE %s ORDER BY source_path",
                        (f"{folder}%",),
                    )
            else:
                rows = backend.fetchall(
                    "SELECT DISTINCT source_path FROM vault_chunks ORDER BY source_path"
                )

            if not rows:
                return "No indexed notes found." + (f" (folder filter: {folder})" if folder else "")

            paths = [r[0] for r in rows]
            result = f"{len(paths)} indexed notes"
            if folder:
                result += f" in {folder}/"
            result += ":\n\n"
            result += "\n".join(f"  {p}" for p in paths)
            return result
        except Exception as e:
            return f"Error listing notes: {e}"
        finally:
            backend.close()

    @mcp.tool()
    def get_vault_info() -> str:
        """Return server identity: vault path, tier, chunk count, installed caps and tools.

        Use this to confirm which vault this MCP server is connected to.
        Useful for debugging multi-vault or multi-IDE setups.
        """
        backend = get_backend(state)
        chunk_count = 0
        try:
            backend.connect()
            chunk_count = backend.count_rows("vault_chunks")
        except Exception:
            pass
        finally:
            backend.close()

        installed_caps = list(_state.get_installed_caps().keys())
        installed_tools = list(_state.get_installed_tools().keys())

        import json
        return json.dumps({
            "vault_path": vault_path,
            "tier": state.get("tier", 0),
            "chunks": chunk_count,
            "caps": installed_caps,
            "tools": installed_tools,
        }, indent=2)

    @mcp.tool()
    def save_note(title: str, content: str, project: str = "none", tags: str = "", authored_by: str = "antigravity-session") -> str:
        """Save a note to the vault inbox for automatic filing and indexing.

        The intake pipeline will pick it up, validate frontmatter, move it to
        the correct project folder, and index it for search.

        Args:
            title: Short descriptive title (used in filename slug).
            content: Full markdown body of the note (below the frontmatter).
            project: Project name matching a folder in projects/ (default "none").
            tags: Comma-separated topic tags (e.g. "rag,embeddings,llm").
            authored_by: Author identifier (default "antigravity-session").
        """
        import re
        from datetime import datetime, timezone

        # Build slug from title
        slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:60]
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        # Resolve inbox dir
        inbox_dir = Path(vault_path) / "agents" / "general" / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{date_str}-{slug}.md"
        filepath = inbox_dir / filename

        # Build tag list
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        tags_yaml = ", ".join(tag_list)

        # Assemble the note with frontmatter
        note = f"""---
authored_by: [{authored_by}]
created_at: {timestamp}
project: {project}
topics: [{tags_yaml}]
type: note
urgency: normal
actionability: info-only
podcast_potential: background
---

{content}
"""
        filepath.write_text(note)
        return f"Saved: {filepath}"

    @mcp.tool()
    def set_vault_path(new_path: str) -> str:
        """Update the vault path this server points to.

        Persists the change to ~/.ambientrag/state.json and takes effect
        immediately for all subsequent tool calls (no restart needed).

        Args:
            new_path: Absolute path to the Obsidian vault directory.
        """
        nonlocal vault_path

        resolved = str(Path(new_path).expanduser().resolve())
        if not Path(resolved).is_dir():
            return f"Error: '{resolved}' is not a directory."

        vault_path = resolved
        state["vault_path"] = resolved
        _state.save_state(state)

        return f"Vault path updated to: {resolved}"

    # ------------------------------------------------------------------
    # REST endpoints (for curl testing / health checks)
    # ------------------------------------------------------------------

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        """Health check endpoint."""
        backend = get_backend(state)
        chunk_count = 0
        try:
            backend.connect()
            chunk_count = backend.count_rows("vault_chunks")
        except Exception:
            pass
        finally:
            backend.close()

        installed_caps = list(_state.get_installed_caps().keys())
        installed_tools = list(_state.get_installed_tools().keys())

        return JSONResponse({
            "status": "ok",
            "vault": vault_path,
            "tier": state.get("tier", 0),
            "chunks": chunk_count,
            "caps": installed_caps,
            "tools": installed_tools,
        })

    @mcp.custom_route("/api/search", methods=["GET"])
    async def api_search(request: Request) -> JSONResponse:
        """REST search endpoint for curl testing."""
        query = request.query_params.get("q", "")
        limit_str = request.query_params.get("limit", "5")
        try:
            limit = int(limit_str)
        except ValueError:
            limit = 5

        if not query:
            return JSONResponse(
                {"error": "Missing 'q' parameter. Usage: /api/search?q=your+query"},
                status_code=400,
            )

        backend = get_backend(state)
        try:
            backend.connect()

            chunk_count = 0
            try:
                chunk_count = backend.count_rows("vault_chunks")
            except Exception:
                pass

            start = time.perf_counter()
            results = _search_fts(backend, query, limit)
            elapsed_ms = (time.perf_counter() - start) * 1000

            _log_search_metric(backend, query, elapsed_ms, len(results))

            return JSONResponse({
                "query": query,
                "results": results,
                "total_chunks": chunk_count,
                "elapsed_ms": round(elapsed_ms, 1),
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        finally:
            backend.close()

    # ------------------------------------------------------------------
    # Token Dashboard — API + HTML
    # ------------------------------------------------------------------

    @mcp.custom_route("/api/tokens/recent", methods=["GET"])
    async def tokens_recent(request: Request) -> JSONResponse:
        """Return the last N tool calls from mcp_token_log."""
        if not _state.is_tool_active("002"):
            return JSONResponse({"error": "Token Hygiene (TOOL-002) not installed"}, status_code=404)

        limit_str = request.query_params.get("limit", "50")
        try:
            limit = min(int(limit_str), 200)
        except ValueError:
            limit = 50

        tier = state.get("tier", 0)
        backend = get_backend(state)
        try:
            backend.connect()
            if tier == 0:
                rows = backend.fetchall(
                    "SELECT id, session_id, session_label, tool_name, tokens_estimated, called_at "
                    "FROM mcp_token_log ORDER BY called_at DESC LIMIT ?",
                    (limit,),
                )
            else:
                rows = backend.fetchall(
                    "SELECT id, session_id, session_label, tool_name, tokens_estimated, called_at "
                    "FROM mcp_token_log ORDER BY called_at DESC LIMIT %s",
                    (limit,),
                )

            results = []
            for r in rows:
                results.append({
                    "id": r[0],
                    "session_id": r[1],
                    "session_label": r[2],
                    "tool_name": r[3],
                    "tokens_estimated": r[4],
                    "called_at": str(r[5]) if r[5] else None,
                })
            return JSONResponse({"calls": results, "count": len(results)})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        finally:
            backend.close()

    @mcp.custom_route("/api/tokens/summary", methods=["GET"])
    async def tokens_summary(request: Request) -> JSONResponse:
        """Aggregated token stats: totals, per-session, per-tool breakdown."""
        if not _state.is_tool_active("002"):
            return JSONResponse({"error": "Token Hygiene (TOOL-002) not installed"}, status_code=404)

        tier = state.get("tier", 0)
        backend = get_backend(state)
        try:
            backend.connect()

            # --- Today filter ---
            if tier == 0:
                today_filter = "WHERE called_at >= date('now')"
            else:
                today_filter = "WHERE called_at >= CURRENT_DATE"

            # Total tokens today
            row = backend.fetchall(
                f"SELECT COALESCE(SUM(tokens_estimated), 0), COUNT(*) "
                f"FROM mcp_token_log {today_filter}"
            )
            total_tokens_today = row[0][0] if row else 0
            total_calls_today = row[0][1] if row else 0

            # Total sessions today
            row = backend.fetchall(
                f"SELECT COUNT(DISTINCT session_id) FROM mcp_token_log {today_filter}"
            )
            total_sessions_today = row[0][0] if row else 0

            # Per-tool breakdown (today)
            tool_rows = backend.fetchall(
                f"SELECT tool_name, SUM(tokens_estimated) as total, COUNT(*) as calls "
                f"FROM mcp_token_log {today_filter} "
                f"GROUP BY tool_name ORDER BY total DESC"
            )
            tool_breakdown = [
                {"tool": r[0], "tokens": r[1], "calls": r[2]} for r in tool_rows
            ]

            # Per-session summary (today, top 20)
            if tier == 0:
                session_rows = backend.fetchall(
                    f"SELECT session_id, session_label, SUM(tokens_estimated) as total, "
                    f"COUNT(*) as calls, MIN(called_at) as first_call, MAX(called_at) as last_call "
                    f"FROM mcp_token_log {today_filter} "
                    f"GROUP BY session_id ORDER BY last_call DESC LIMIT 20"
                )
            else:
                session_rows = backend.fetchall(
                    f"SELECT session_id, session_label, SUM(tokens_estimated) as total, "
                    f"COUNT(*) as calls, MIN(called_at) as first_call, MAX(called_at) as last_call "
                    f"FROM mcp_token_log {today_filter} "
                    f"GROUP BY session_id, session_label ORDER BY last_call DESC LIMIT 20"
                )
            sessions = [
                {
                    "session_id": r[0],
                    "label": r[1],
                    "tokens": r[2],
                    "calls": r[3],
                    "first_call": str(r[4]) if r[4] else None,
                    "last_call": str(r[5]) if r[5] else None,
                }
                for r in session_rows
            ]

            # All-time totals
            row = backend.fetchall(
                "SELECT COALESCE(SUM(tokens_estimated), 0), COUNT(*) FROM mcp_token_log"
            )
            total_tokens_all = row[0][0] if row else 0
            total_calls_all = row[0][1] if row else 0

            return JSONResponse({
                "today": {
                    "tokens": total_tokens_today,
                    "calls": total_calls_today,
                    "sessions": total_sessions_today,
                },
                "all_time": {
                    "tokens": total_tokens_all,
                    "calls": total_calls_all,
                },
                "tool_breakdown": tool_breakdown,
                "sessions": sessions,
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        finally:
            backend.close()

    @mcp.custom_route("/dashboard", methods=["GET"])
    async def dashboard(request: Request) -> HTMLResponse:
        """Token monitoring dashboard — the AmbientRAG a-ha moment."""
        html = _DASHBOARD_HTML.replace("{{HOST}}", f"{host}:{port}")
        return HTMLResponse(html)

    return mcp


def start_server(host: str = "127.0.0.1", port: int = 8100, transport: str = "streamable-http") -> None:
    """Start the AmbientRAG MCP server (blocking).

    Loads state, creates the server, and runs with the specified transport.
    Defaults to streamable-http (MCP standard). Use --transport sse for legacy clients.
    """
    state = _state.load_state()
    mcp = create_server(state, host=host, port=port)
    mcp.run(transport=transport)
