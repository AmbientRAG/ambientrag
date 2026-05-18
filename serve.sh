#!/bin/bash
# Start AmbientRAG MCP server and wait until healthy.
# Usage: bash serve.sh [--port 8100]
PORT="${1:-8100}"
TIMEOUT=120

source .venv/bin/activate 2>/dev/null

# Seed demo notes if not already present (first run)
VAULT_PATH=$(python3 -c "from ambientrag.state import get_vault_path; print(get_vault_path() or '')")
if [ -n "$VAULT_PATH" ] && [ -z "$(ls "$VAULT_PATH/_demo/"*.md 2>/dev/null)" ]; then
    echo "  [INFO] No demo notes found — seeding..."
    ambientrag demo seed
fi

# Index vault before starting (picks up new/changed files)
echo "  [INFO] Indexing vault..."
ambientrag index
echo ""

# Kill anything already on the port
if lsof -ti :"$PORT" >/dev/null 2>&1; then
    echo "  [WARN] Port $PORT in use — stopping existing process"
    kill $(lsof -ti :"$PORT") 2>/dev/null
    sleep 1
fi

ambientrag serve --port "$PORT" &
SERVER_PID=$!

ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if ! kill -0 $SERVER_PID 2>/dev/null; then
        echo "  [FAIL] MCP server process died. Check port $PORT or run: ambientrag doctor"
        exit 1
    fi
    if curl -sf http://localhost:$PORT/health >/dev/null 2>&1; then
        break
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
done

if [ $ELAPSED -ge $TIMEOUT ]; then
    echo "  [FAIL] MCP server failed to start within ${TIMEOUT}s"
    echo "         Run: ambientrag doctor"
    kill $SERVER_PID 2>/dev/null
    exit 1
fi

echo ""
echo "  [OK] MCP server is live (PID $SERVER_PID, port $PORT)"
curl -s http://localhost:$PORT/health | python3 -m json.tool
echo ""

# Print Getting Started using Rich (matches the rest of the CLI output)
python3 -c "
from rich.console import Console
from rich.panel import Panel
Console().print(Panel(
    '[bold green]MCP server is running.[/bold green]\n\n'
    '[bold]Try it now:[/bold]\n'
    '  curl \"http://localhost:$PORT/api/search?q=hello\"\n\n'
    '[bold]Connect your AI assistant:[/bold]\n'
    '  ambientrag connect --list\n\n'
    '[bold]More commands:[/bold]\n'
    '  ambientrag cap list              — available capabilities\n'
    '  ambientrag tool install 001      — search performance metrics\n'
    '  ambientrag bench --save          — benchmark your search latency\n'
    '  ambientrag diagram               — architecture diagram (Mermaid)\n\n'
    '[bold]Stop server:[/bold]\n'
    '  kill $SERVER_PID',
    title='Getting Started',
))
"
