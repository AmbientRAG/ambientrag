---
name: vault-setup
description: Set up or troubleshoot the AmbientRAG knowledge vault connection. Use when the user says "set up the vault", "connect to the vault", "vault not working", "MCP not connecting", "configure AmbientRAG", or when vault tools return errors.
---

# Vault Setup — AmbientRAG Connection Bootstrap

Help the user get their AmbientRAG knowledge vault connected and working.

## Quick Health Check

If the user says the vault isn't working, run diagnostics in order:

### 1. Check vault identity

Call `get_vault_info()` — if this works, MCP is connected. Check the `vault_path` is correct.

If `get_vault_info()` fails, MCP isn't connected. Continue to step 2.

### 2. Check if MCP is configured
Look for `~/.gemini/antigravity/mcp_config.json`. It should contain:

```json
{
  "mcpServers": {
    "ambientrag": {
      "serverURL": "http://localhost:PORT/mcp"
    }
  }
}
```

The port depends on their setup (commonly 8100).

### 3. Check if the server is running
```bash
curl -s http://localhost:8100/health
```

If this returns JSON with `"status": "ok"`, the server is healthy. If it errors, the server needs to be started.

### 4. Fix wrong vault path

If `get_vault_info()` shows the wrong path, call `set_vault_path("/correct/path/to/vault")` to repoint it live. No restart needed.

## First-Time Setup

If nothing is configured yet, walk them through:

1. **Install AmbientRAG** — download and run the installer:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   curl -sO https://storage.googleapis.com/mark-vault-transfer/ambientrag-v24.zip
   unzip -o ambientrag-v24.zip -d ambientrag
   cd ambientrag && uv venv --python 3.12 .venv
   source .venv/bin/activate && uv pip install .
   ambientrag doctor && ambientrag init --tier 0 --vault-path ~/ambientrag
   bash serve.sh
   ```

2. **Install PLAT-001a skills** — download and run:
   ```bash
   curl -sO https://storage.googleapis.com/mark-vault-transfer/plat-001a-v2.zip
   unzip -o plat-001a-v2.zip && cd plat-001a && bash install.sh
   ```

3. **Verify** — search for something: "search the vault for getting started"

## Common Issues

| Symptom | Fix |
|---------|-----|
| "MCP Error" in Antigravity | Server not running. Start with `bash serve.sh` |
| "session not found" | Server running wrong transport. Needs `streamable-http` for Antigravity |
| Search returns nothing | Database empty. Run `ambientrag init` or check indexer |
| "serverURL or command must be specified" | Config key must be `serverURL` (capital R, capital L) |
| Connection refused | Wrong port. Check what port `serve.sh` is using |
| Wrong vault path | Call `set_vault_path("/correct/path")` or check `~/.ambientrag/state.json` |
| `get_vault_info()` shows wrong dir | Call `set_vault_path()` to fix — takes effect immediately |

## Key Insight

Antigravity speaks **streamable HTTP** (not SSE). The MCP config must use `serverURL` pointing at the `/mcp` endpoint. Do NOT use `/sse` — that's a different protocol.
