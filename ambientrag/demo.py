"""AmbientRAG demo command group — seed, clean, and status for demo notes.

Demo notes are CAP-aware: frontmatter richness scales with installed capabilities.
- CAP-001 only: basic frontmatter (type, tags, created)
- CAP-002 installed: adds hyde_questions, hyde_caveman, related_topics, entities
- CAP-004 installed: adds document_kind, valid_from, valid_until

All 5 notes are [[wikilinked]] so Obsidian graph view shows a constellation.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from ambientrag.utils import print_error, print_info, print_success, print_warning

console = Console()

DEMO_DIR_NAME = "_demo"

# ---------------------------------------------------------------------------
# Demo note definitions — content + per-CAP frontmatter layers
# ---------------------------------------------------------------------------

DEMO_NOTES = {
    "hello-world.md": {
        "base_frontmatter": {
            "type": "demo",
            "status": "active",
            "tags": ["demo", "ambientrag", "welcome"],
            "created": "2026-05-12",
            "demo_generated": True,
        },
        "enrichment": {
            "hyde_questions": [
                "what is AmbientRAG hello world?",
                "how do I get started with AmbientRAG?",
                "first search in ambient retrieval",
                "AmbientRAG welcome tutorial",
            ],
            "hyde_caveman": "Welcome note. First thing user sees after install. Search found this. Links to four other demo notes showing how search, enrichment, temporal scoring, and next steps work.",
            "related_topics": ["getting-started", "onboarding", "first-search", "tutorial"],
            "entities": ["AmbientRAG", "MCP", "pgvector", "Obsidian"],
        },
        "temporal": {
            "document_kind": "static",
        },
        "body": """\
# Hello World

You searched, and AmbientRAG found this. That's the whole pitch.

This note exists to prove that your install works. Behind the scenes, your query was embedded into a vector, compared against every chunk in the vault using cosine similarity, and this note won. If you're reading this, the pipeline is live.

## How You Got Here

1. You installed AmbientRAG and ran `ambientrag demo seed`
2. These demo notes were indexed by your MCP server
3. You searched — and the system returned this note

## The Demo Constellation

These five notes are interconnected. Open Obsidian's graph view to see them:

- [[how-search-works]] — explains the hybrid retrieval under the hood
- [[enrichment-and-freshness]] — how notes get smarter metadata over time
- [[your-first-note]] — try creating your own note and watching it appear
- [[whats-next]] — the CAP roadmap and where to go from here

## What to Try

Search for "how does search work" — you should get [[how-search-works]].
Search for "coffee" — you should get nothing from these demo notes.
That's semantic filtering: meaning matters, not just keywords.
""",
    },
    "how-search-works.md": {
        "base_frontmatter": {
            "type": "demo",
            "status": "active",
            "tags": ["demo", "ambientrag", "search", "technical"],
            "created": "2026-05-12",
            "demo_generated": True,
        },
        "enrichment": {
            "hyde_questions": [
                "how does AmbientRAG search work?",
                "hybrid retrieval semantic keyword search",
                "what is reciprocal rank fusion?",
                "how does vector search find relevant notes?",
                "cosine similarity vs keyword matching",
            ],
            "hyde_caveman": "Explains hybrid search. Vector embeddings find meaning, keyword search finds exact terms. RRF fuses both. pgvector HNSW index for speed. Links to hello-world and enrichment.",
            "related_topics": ["vector-search", "cosine-similarity", "full-text-search", "HNSW", "reciprocal-rank-fusion"],
            "entities": ["pgvector", "HNSW", "GIN", "Harrier", "RRF"],
        },
        "temporal": {
            "document_kind": "static",
        },
        "body": """\
# How Search Works

When you search AmbientRAG, two systems fire simultaneously.

## The Two Engines

**Semantic search** embeds your query into a 1024-dimensional vector using the same model that embedded your notes. It finds notes that *mean* the same thing, even if the words differ. Searching "getting started" can find a note titled "Hello World" because the concepts overlap.

**Keyword search** uses PostgreSQL's built-in full-text search with a GIN index. It finds notes containing your exact terms. Searching "pgvector" finds notes mentioning pgvector, even if the semantic meaning is about something else entirely.

## Reciprocal Rank Fusion

Neither engine is perfect alone. Semantic search misses exact terms. Keyword search misses meaning. AmbientRAG fuses their results using Reciprocal Rank Fusion (RRF):

```
rrf_score = 1/(k + semantic_rank) + 1/(k + keyword_rank)
```

A note ranked #1 by both engines scores highest. A note ranked #1 by one but absent from the other still surfaces — it just scores lower.

## Why This Matters

Pure vector search would return "Hello World" for "getting started" but miss a note titled exactly "Getting Started Guide" that uses different vocabulary in the body. Hybrid search catches both.

See also: [[hello-world]] | [[enrichment-and-freshness]]
""",
    },
    "enrichment-and-freshness.md": {
        "base_frontmatter": {
            "type": "demo",
            "status": "active",
            "tags": ["demo", "ambientrag", "enrichment", "temporal"],
            "created": "2026-05-12",
            "demo_generated": True,
        },
        "enrichment": {
            "hyde_questions": [
                "how does AmbientRAG enrich notes?",
                "what is hyde_caveman enrichment?",
                "how does document freshness affect search?",
                "what are hypothetical document embeddings?",
                "how does temporal scoring work in AmbientRAG?",
            ],
            "hyde_caveman": "Explains enrichment pipeline and temporal scoring. HyDE questions expand search surface. Caveman summaries save tokens. Temporal scoring ranks fresh notes higher. Document kinds: static, versioned, event. Links to how-search-works and whats-next.",
            "related_topics": ["HyDE", "hypothetical-document-embeddings", "document-aging", "decay-profiles", "caveman-cache"],
            "entities": ["HyDE", "CAP-002", "CAP-004", "hyde_caveman", "hyde_questions"],
        },
        "temporal": {
            "document_kind": "versioned",
            "valid_from": "2026-05-12",
        },
        "body": """\
# Enrichment & Freshness

Raw notes embed poorly. A note titled "eGPU Research" with three bullet points produces a weak vector. AmbientRAG fixes this with two layers.

## Enrichment (CAP-002)

When a note is created, the system generates synthetic metadata:

- **hyde_questions** — "What would you search to find this note in 6 months?" Multiple phrasings expand the search surface area.
- **hyde_caveman** — A dense, compressed summary in "caveman speak." Maximum information, minimum tokens.
- **related_topics** — Semantic synonyms not in the note text. Bridges vocabulary gaps.
- **entities** — Proper nouns for exact-match filtering.

This metadata is embedded alongside the note content, making the vector richer and more findable.

## Freshness (CAP-004)

Not all notes age the same way. AmbientRAG classifies documents into three kinds:

- **STATIC** — Architecture decisions, reference docs. Never penalized for age.
- **VERSIONED** — Project status, policies. New versions outrank old ones via time decay.
- **EVENT** — Meeting notes, deadlines. Boosted while active, dropped after expiry.

The key insight: a versioned policy with an effective date looks identical to a time-bounded event without this classification. The system would produce the wrong result for a right-sounding reason.

See also: [[how-search-works]] | [[whats-next]]
""",
    },
    "your-first-note.md": {
        "base_frontmatter": {
            "type": "demo",
            "status": "active",
            "tags": ["demo", "ambientrag", "tutorial", "hands-on"],
            "created": "2026-05-12",
            "demo_generated": True,
        },
        "enrichment": {
            "hyde_questions": [
                "how do I create my first note in AmbientRAG?",
                "how to add a note to the vault?",
                "write my own note and search for it",
                "test that AmbientRAG indexing works",
            ],
            "hyde_caveman": "Hands-on tutorial. Create markdown file, add frontmatter, wait for indexer, search for it. Proves the pipeline works end-to-end with your own content. Links to hello-world.",
            "related_topics": ["note-creation", "frontmatter", "indexing", "hands-on-tutorial"],
            "entities": ["Obsidian", "index_vault.py", "YAML"],
        },
        "temporal": {
            "document_kind": "static",
        },
        "body": """\
# Your First Note

The demo notes prove the system works. Now prove it with your own content.

## Step 1: Create a Note

Create a markdown file anywhere in your vault. Add minimal frontmatter:

```yaml
---
type: note
status: active
tags: [my-first-note]
created: 2026-05-12
---
```

Write a paragraph about something you know. Your favorite programming language. A recipe. A project idea. Anything with enough substance to embed meaningfully (50+ words).

## Step 2: Wait for Indexing

If the file watcher is running, your note will be indexed within seconds. Otherwise, run the indexer manually.

## Step 3: Search for It

Search for a concept from your note — but use different words than you wrote. If you wrote about Python list comprehensions, search for "filtering collections in Python." If the system finds your note, semantic search is working.

## Step 4: The Aha Moment

The moment your own note appears in search results for a query that shares zero keywords with it — that's when it clicks. The system understands meaning, not just words.

See also: [[hello-world]] | [[whats-next]]
""",
    },
    "whats-next.md": {
        "base_frontmatter": {
            "type": "demo",
            "status": "active",
            "tags": ["demo", "ambientrag", "roadmap", "capabilities"],
            "created": "2026-05-12",
            "demo_generated": True,
        },
        "enrichment": {
            "hyde_questions": [
                "what capabilities can I add to AmbientRAG?",
                "AmbientRAG roadmap and next steps",
                "what CAPs are available?",
                "how to extend AmbientRAG after install?",
                "what comes after hello world in AmbientRAG?",
            ],
            "hyde_caveman": "Roadmap note. Lists all CAPs and what they add. Progressive capability — install what you need. Reranker for precision, token hygiene for cost. Clean up demo with ambientrag demo clean. Links to all four other demo notes.",
            "related_topics": ["capabilities", "modular-architecture", "progressive-enhancement", "CAP-system"],
            "entities": ["CAP-001", "CAP-002", "CAP-003", "CAP-004", "CAP-005"],
        },
        "temporal": {
            "document_kind": "static",
        },
        "body": """\
# What's Next

You've seen the basics. AmbientRAG is designed to grow with you.

## The CAP System

Every enhancement is a numbered Capability. Install what you need, skip what you don't:

| CAP | Name | What It Adds |
|-----|------|-------------|
| 001 | Vector Search | The foundation — hybrid semantic + keyword search |
| 002 | Enrichment | HyDE questions, caveman summaries, richer vectors |
| 003 | Tiered Retrieval | Three-layer progressive disclosure for token savings |
| 004 | Temporal Scoring | Document freshness, aging, validity windows |
| 005 | Reranker | Cross-encoder precision re-scoring (GPU-accelerated) |

Run `ambientrag cap list` to see what's installed and available.

## Progressive Power

These demo notes demonstrate the progressive model:

- With **CAP-001 only**, search works on basic embeddings and keywords
- Add **CAP-002**, re-seed the demos, and search quality visibly improves — queries find notes using different vocabulary
- Add **CAP-004**, re-seed again, and time-sensitive content ranks correctly — the meeting notes surface for "recent updates" while they're fresh

Each CAP install + `ambientrag demo seed` shows you the difference.

## Connect Your Tools

```bash
ambientrag connect codex        # Codex CLI integration
ambientrag connect claude-code  # Claude Code integration
```

## Clean Up

When you're done exploring:

```bash
ambientrag demo clean
```

This removes all demo notes. Your real notes are never touched.

See also: [[hello-world]] | [[how-search-works]] | [[enrichment-and-freshness]] | [[your-first-note]]
""",
    },
}


def _build_frontmatter(note_key: str, note: dict, installed_caps: set) -> dict:
    """Build full frontmatter — all layers always included.

    The frontmatter IS the sales pitch. Fields like hyde_caveman and
    document_kind are visible in the note even before the CAP that uses
    them is installed. Users see the fields, get curious, install the CAP,
    and watch search get better. The unused fields cost nothing — they're
    just YAML until the right CAP activates them.
    """
    fm = dict(note["base_frontmatter"])

    if "enrichment" in note:
        fm.update(note["enrichment"])

    if "temporal" in note:
        fm.update(note["temporal"])

    return fm


def _format_frontmatter(fm: dict) -> str:
    """Format frontmatter dict as YAML string."""
    lines = ["---"]
    for key, value in fm.items():
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, list):
            if all(isinstance(v, str) and len(v) < 60 for v in value):
                items = ", ".join(str(v) for v in value)
                lines.append(f"{key}: [{items}]")
            else:
                # Multi-line list for long items (hyde_questions)
                lines.append(f"{key}:")
                for v in value:
                    lines.append(f'  - "{v}"')
        elif isinstance(value, str) and len(value) > 80:
            # Multi-line string for hyde_caveman etc.
            lines.append(f"{key}: >-")
            lines.append(f"  {value}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def _get_vault_path(vault_path: str | None) -> Path:
    """Resolve vault path from option, state, or cwd."""
    if vault_path:
        return Path(vault_path).resolve()
    try:
        import ambientrag.state as _state
        if _state.is_initialized():
            vp = _state.get_vault_path()
            if vp:
                return vp
    except Exception:
        pass
    return Path.cwd()


def _get_installed_caps() -> set:
    """Get installed CAP IDs as a set of strings."""
    try:
        import ambientrag.state as _state
        caps = _state.get_installed_caps()
        return {k for k, v in caps.items() if v.get("enabled", True)}
    except Exception:
        return set()


def _has_demo_marker(file_path: Path) -> bool:
    """Check if a markdown file has demo_generated: true in frontmatter."""
    try:
        text = file_path.read_text(encoding="utf-8")
        fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        if fm_match:
            return "demo_generated: true" in fm_match.group(1)
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Click command group
# ---------------------------------------------------------------------------

@click.group()
def demo():
    """Manage demo notes for showcasing AmbientRAG."""
    pass


@demo.command()
@click.option(
    "--vault-path",
    type=click.Path(),
    default=None,
    help="Path to Obsidian vault (default: initialized vault or cwd)",
)
def seed(vault_path: str | None):
    """Generate demo notes in the vault's _demo/ folder.

    Frontmatter scales with installed CAPs:
    - CAP-001: basic tags and metadata
    - CAP-002: adds enrichment (hyde_questions, caveman summaries)
    - CAP-004: adds temporal scoring (document_kind, validity windows)

    Re-run after installing new CAPs to see the difference.
    """
    resolved = _get_vault_path(vault_path)
    demo_dir = resolved / DEMO_DIR_NAME
    installed = _get_installed_caps()

    if demo_dir.exists():
        existing = list(demo_dir.glob("*.md"))
        if existing:
            print_warning(f"Demo directory exists with {len(existing)} files — overwriting.")

    demo_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for filename, note in DEMO_NOTES.items():
        file_path = demo_dir / filename
        fm = _build_frontmatter(filename, note, installed)
        content = _format_frontmatter(fm) + "\n" + note["body"]
        file_path.write_text(content, encoding="utf-8")
        count += 1

    print_success(f"Seeded {count} demo notes to {demo_dir}")
    console.print("")

    console.print("[bold]Frontmatter includes all layers:[/bold]")
    console.print(f"  {'[green]✓ active[/green]' if True else ''} CAP-001 — tags, type, created")
    e_status = "[green]✓ active[/green]" if "002" in installed else "[yellow]visible but dormant[/yellow]"
    console.print(f"  {e_status} CAP-002 — hyde_questions, hyde_caveman, related_topics")
    t_status = "[green]✓ active[/green]" if "004" in installed else "[yellow]visible but dormant[/yellow]"
    console.print(f"  {t_status} CAP-004 — document_kind, valid_from")

    dormant = []
    if "002" not in installed:
        dormant.append("002")
    if "004" not in installed:
        dormant.append("004")

    if dormant:
        console.print("")
        console.print("[dim]Dormant fields are in the notes — open one in Obsidian to see them.[/dim]")
        console.print(f"[dim]Install CAP-{' + CAP-'.join(dormant)} to activate them in search.[/dim]")

    console.print("")
    console.print("[bold]Open Obsidian graph view[/bold] to see the constellation (5 linked notes).")
    console.print("")
    console.print("[bold]Try these searches:[/bold]")
    console.print('  "hello world"                          → hello-world.md')
    console.print('  "how does search work"                 → how-search-works.md')
    console.print('  "document freshness and aging"         → enrichment-and-freshness.md')
    console.print('  "create my own note"                   → your-first-note.md')
    console.print('  "what capabilities can I add"          → whats-next.md')


@demo.command()
@click.option(
    "--vault-path",
    type=click.Path(),
    default=None,
    help="Path to Obsidian vault (default: initialized vault or cwd)",
)
def clean(vault_path: str | None):
    """Remove demo notes from the vault."""
    resolved = _get_vault_path(vault_path)
    demo_dir = resolved / DEMO_DIR_NAME

    if not demo_dir.exists():
        print_info("No demo directory found — nothing to clean.")
        return

    md_files = list(demo_dir.glob("*.md"))
    safe_to_delete = []
    unsafe_files = []

    for f in md_files:
        if _has_demo_marker(f):
            safe_to_delete.append(f)
        else:
            unsafe_files.append(f)

    if unsafe_files:
        print_warning(
            f"Found {len(unsafe_files)} file(s) WITHOUT demo_generated marker — skipping:"
        )
        for f in unsafe_files:
            console.print(f"    {f.name}")

    for f in safe_to_delete:
        f.unlink()

    remaining = list(demo_dir.iterdir())
    if not remaining:
        demo_dir.rmdir()
        print_success(f"Removed {len(safe_to_delete)} demo notes and {DEMO_DIR_NAME}/ directory.")
    else:
        print_success(f"Removed {len(safe_to_delete)} demo notes. Directory kept ({len(remaining)} files remain).")


@demo.command()
@click.option(
    "--vault-path",
    type=click.Path(),
    default=None,
    help="Path to Obsidian vault (default: initialized vault or cwd)",
)
def status(vault_path: str | None):
    """Show whether demo files exist and their count."""
    resolved = _get_vault_path(vault_path)
    demo_dir = resolved / DEMO_DIR_NAME

    if not demo_dir.exists():
        print_info(f"No demo directory at {demo_dir}")
        return

    md_files = list(demo_dir.glob("*.md"))
    demo_files = [f for f in md_files if _has_demo_marker(f)]

    t = Table(title="Demo Status", show_header=True)
    t.add_column("Metric")
    t.add_column("Value")
    t.add_row("Path", str(demo_dir))
    t.add_row("Demo notes", str(len(demo_files)))
    t.add_row("Wikilinked", "Yes (5-note constellation)")

    installed = _get_installed_caps()
    layers = ["CAP-001 (basic)"]
    if "002" in installed:
        layers.append("CAP-002 (enrichment)")
    if "004" in installed:
        layers.append("CAP-004 (temporal)")
    t.add_row("Frontmatter layers", ", ".join(layers))

    console.print(t)

    if demo_files:
        console.print("\n[bold]Demo notes:[/bold]")
        for f in sorted(demo_files):
            console.print(f"  [[{f.stem}]]")
