#!/usr/bin/env bash
# release.sh — sync approved files from dev repo to public repo
# Run from ambientrag-public/
set -euo pipefail

DEV="../ambientrag"
PUBLIC="."

if [ ! -d "$DEV/ambientrag" ]; then
  echo "ERROR: Dev repo not found at $DEV"
  exit 1
fi

echo "Syncing from $DEV → $PUBLIC"
echo "Only approved files will be copied."
echo ""

# Approved file list — add files here as you release new CAPs
APPROVED_FILES=(
  .env.example
  .gitignore
  LICENSE
  README.md
  pyproject.toml
  setup.py
  install.sh
  serve.sh
  ambientrag/__init__.py
  ambientrag/__main__.py
  ambientrag/cli.py
  ambientrag/utils.py
  ambientrag/search.py
  ambientrag/server.py
  ambientrag/state.py
  ambientrag/connect.py
  ambientrag/demo.py
  ambientrag/doctor.py
  ambientrag/tier.py
  ambientrag/diagram.py
  ambientrag/db/__init__.py
  ambientrag/db/base.py
  ambientrag/db/factory.py
  ambientrag/db/pg_backend.py
  ambientrag/db/sqlite_backend.py
  ambientrag/caps/__init__.py
  ambientrag/caps/registry.py
  ambientrag/caps/manifest.json
  ambientrag/caps/cap_001_vector_search/__init__.py
  ambientrag/caps/cap_001_vector_search/install.py
  ambientrag/caps/cap_001_vector_search/schema.sql
  ambientrag/caps/cap_001_vector_search/uninstall.py
  ambientrag/caps/cap_001_vector_search/verify.py
  docs/README.md
  docs/architecture.md
  docs/capabilities.md
  docs/cli-reference.md
  docs/demo.md
  docs/installation.md
  docs/integrations.md
  ambientrag/plats/README.md
  ambientrag/plats/plat_001a_antigravity/install.sh
  ambientrag/plats/plat_001a_antigravity/mcp_config.json
  ambientrag/plats/plat_001a_antigravity/platform.yaml
  ambientrag/plats/plat_001a_antigravity/skills/vault-search/SKILL.md
  ambientrag/plats/plat_001a_antigravity/skills/vault-setup/SKILL.md
  ambientrag/plats/plat_001a_antigravity/skills/document-this/SKILL.md
  tests/qa_caps.py
  tests/qa_publish.py
)

COPIED=0
for f in "${APPROVED_FILES[@]}"; do
  if [ -f "$DEV/$f" ]; then
    mkdir -p "$(dirname "$PUBLIC/$f")"
    cp "$DEV/$f" "$PUBLIC/$f"
    ((COPIED++))
  else
    echo "WARN: $f not found in dev repo, skipping"
  fi
done

echo ""
echo "Copied $COPIED files."
echo ""
echo "Next steps:"
echo "  git diff          # review changes"
echo "  git add -p        # stage selectively"
echo "  git commit"
echo "  git push"
