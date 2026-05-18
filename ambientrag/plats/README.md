# Platform Adapters (PLATs)

Platform-specific installation packages that connect AmbientRAG to different AI coding IDEs.

Each PLAT contains the skills, configs, and install scripts needed to make AmbientRAG work with a specific platform. The MCP server is platform-agnostic — PLATs handle the client-side integration.

## Available PLATs

| ID | Platform | Status | What it installs |
|----|----------|--------|------------------|
| PLAT-001a | Antigravity (Gemini) | Deployed | 3 skills + mcp_config.json |
| PLAT-001c | Claude Code | Planned | CLAUDE.md snippet |
| PLAT-001o | Codex (OpenAI) | Planned | System prompt / config |
| PLAT-001u | Cursor | Planned | .cursorrules |
| PLAT-001w | Windsurf | Planned | .windsurfrules |
| PLAT-001g | GitHub Copilot | Planned | .github/copilot-instructions.md |

## Directory Structure

```
plats/
  README.md              <- this file
  plat_001a_antigravity/
    platform.yaml        <- metadata, capabilities, quirks
    install.sh           <- user-facing installer
    mcp_config.json      <- MCP client config
    skills/
      vault-search/SKILL.md
      document-this/SKILL.md
      vault-setup/SKILL.md
  plat_001c_claude_code/   (future)
  plat_001o_codex/         (future)
```

## Naming Convention

`PLAT-001x` where `x` is a single letter identifying the platform. `001` is the generation — all current PLATs are gen 1.

## Building a zip for distribution

```bash
cd ambientrag/plats/plat_001a_antigravity
zip -r /tmp/plat-001a-v2.zip . -x '*.DS_Store'
gcloud storage cp /tmp/plat-001a-v2.zip gs://mark-vault-transfer/
```

## Install flow (user-facing)

```bash
curl -sO https://storage.googleapis.com/mark-vault-transfer/plat-001a-v2.zip
unzip -o plat-001a-v2.zip -d plat-001a && cd plat-001a && bash install.sh
```
