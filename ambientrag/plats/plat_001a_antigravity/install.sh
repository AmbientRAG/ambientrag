#!/usr/bin/env bash
# PLAT-001a Installer — Antigravity (Gemini) skills for AmbientRAG
set -euo pipefail

SKILLS_DIR="$HOME/.gemini/antigravity/skills"
MCP_CONFIG="$HOME/.gemini/antigravity/mcp_config.json"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== PLAT-001a: Antigravity Skills for AmbientRAG ==="
echo ""

# Install skills
for skill in vault-search document-this vault-setup; do
    dest="$SKILLS_DIR/$skill"
    mkdir -p "$dest"
    cp "$SCRIPT_DIR/skills/$skill/SKILL.md" "$dest/SKILL.md"
    echo "[OK] Installed skill: $skill"
done

# Install MCP config (backup existing)
if [ -f "$MCP_CONFIG" ]; then
    cp "$MCP_CONFIG" "$MCP_CONFIG.bak"
    echo "[OK] Backed up existing mcp_config.json"
fi
cp "$SCRIPT_DIR/mcp_config.json" "$MCP_CONFIG"
echo "[OK] Installed mcp_config.json"

echo ""
echo "=== Installation complete ==="
echo ""
echo "Skills installed to: $SKILLS_DIR"
echo "MCP config at:       $MCP_CONFIG"
echo ""
echo "Make sure the AmbientRAG server is running:"
echo "  ambientrag serve"
echo ""
echo "Then in Antigravity, try: \"search the vault for getting started\""
