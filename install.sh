#!/bin/bash
# AmbientRAG Installer
# Usage: bash install.sh [--vault-path ~/ambientrag]
set -euo pipefail

VAULT_PATH="${1:-$HOME/ambientrag}"
MCP_PORT=8100
TIMEOUT=120
ZIP_URL="https://storage.googleapis.com/mark-vault-transfer/ambientrag-cli-v17.zip"

# ── Helpers ───────────────────────────────────────────────
info()  { echo "  [INFO]  $1"; }
ok()    { echo "  [OK]    $1"; }
fail()  { echo "  [FAIL]  $1"; exit 1; }
warn()  { echo "  [WARN]  $1"; }

# ── Step 1: Check Python ─────────────────────────────────
echo ""
echo "=== AmbientRAG Installer ==="
echo ""

if command -v python3.12 >/dev/null 2>&1; then
    PYTHON=python3.12
elif command -v python3 >/dev/null 2>&1; then
    PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
        PYTHON=python3
    else
        fail "Python 3.10+ required (found $PY_VERSION). Install with: brew install python@3.12"
    fi
else
    fail "Python not found. Install with: brew install python@3.12"
fi

ok "Found $($PYTHON --version)"

# ── Step 2: Download and unpack ──────────────────────────
info "Downloading AmbientRAG..."
rm -rf ambientrag 2>/dev/null
curl -sfO "$ZIP_URL" || fail "Download failed"
unzip -qo ambientrag-cli-v17.zip || fail "Unzip failed"
cd ambientrag
ok "Downloaded and unpacked"

# ── Step 3: Create venv and install ──────────────────────
info "Creating virtual environment..."
$PYTHON -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel -q
pip install . -q
ok "Installed ambientrag $(ambientrag --version 2>&1 | tail -1)"

# ── Step 4: Doctor check ────────────────────────────────
echo ""
ambientrag doctor
echo ""

# ── Step 5: Initialize vault ─────────────────────────────
info "Initializing vault at $VAULT_PATH..."
ambientrag init --vault-path "$VAULT_PATH"

# ── Step 6: Seed demo notes ─────────────────────────────
ambientrag demo seed

# ── Step 7: Start MCP server ────────────────────────────
info "Starting MCP server..."
ambientrag serve &
SERVER_PID=$!

ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    if curl -sf http://localhost:$MCP_PORT/health >/dev/null 2>&1; then
        break
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
done

echo ""
if [ $ELAPSED -ge $TIMEOUT ]; then
    fail "MCP server failed to start within $TIMEOUT seconds. Run: ambientrag doctor"
fi

ok "MCP server is live (PID $SERVER_PID)"
curl -s http://localhost:$MCP_PORT/health | python3 -m json.tool

# ── Done ─────────────────────────────────────────────────
echo ""
echo "=== AmbientRAG is running ==="
echo ""
echo "  Health:  curl http://localhost:$MCP_PORT/health"
echo "  Search:  curl \"http://localhost:$MCP_PORT/api/search?q=hello\""
echo "  Stop:    kill $SERVER_PID"
echo ""
echo "  Connect your AI assistant:"
echo "    ambientrag connect --list"
echo ""
echo "  More commands:"
echo "    ambientrag cap list"
echo "    ambientrag tool install 001"
echo "    ambientrag bench --save"
echo "    ambientrag diagram"
echo ""
