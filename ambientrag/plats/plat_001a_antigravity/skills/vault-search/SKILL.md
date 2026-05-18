---
name: vault-search
description: Search the knowledge vault for notes, decisions, references, and research. Use when asked to "search the vault", "find notes about", "what do we know about", "look up", or any question that might be answered by existing vault knowledge. Also use PROACTIVELY before answering architecture, planning, or design questions — search first, answer second.
---

# Vault Search — Ambient Knowledge Retrieval

You have access to an Obsidian knowledge vault via the `obsidian-vault` MCP server. It contains project docs, architecture decisions, policies, research findings, and institutional knowledge.

## First Call: Label the Session

On your FIRST vault tool call in any conversation, call `set_session_label("antigravity")`. This tags all token usage so the dashboard can distinguish Antigravity from Claude Code sessions. Do this once, silently — don't mention it to the user.

## Core Behavior: Search -> Load -> Synthesize

Do NOT just return search results. Your job is to **load relevant context and use it to answer.**

### Step 1: Search

Call `search_vault(query)` with the user's question distilled to key concepts. Strip filler words.

If the topic is broad, run 2-3 searches with different phrasings:
- The literal terms ("notification service")
- Related concepts ("event-driven messaging")
- Decision-oriented ("notification architecture decision")

### Step 2: Auto-Load Top Hits

For every result with a score above 0.5, automatically call `get_note(file_path)` to load the full note content. Do NOT ask the user "would you like me to read this?" — just read it.

**Load up to 5 notes.** If more than 5 score above 0.5, prioritize by score and pick the most relevant.

### Step 3: Synthesize

Present your answer grounded in the vault content:

- Lead with the answer, not the search process
- Reference source paths inline so the user can find originals: `(from projects/ambient-rag/spec.md)`
- If vault content contradicts your general knowledge, trust the vault — it has team-specific decisions
- If nothing relevant found, say so honestly and offer to help without vault context

## When to Search Proactively

Search the vault BEFORE answering when the user:
- Asks about architecture or design ("how does X work?", "what's the pattern for Y?")
- References a project by name
- Asks about a past decision ("why did we choose X?", "what was decided about Y?")
- Starts planning work ("let's build", "how should we implement")
- Mentions policies, standards, or conventions

## Available MCP Tools

| Tool | Purpose |
|------|---------|
| `search_vault(query)` | Hybrid semantic + keyword search. Returns ranked chunks. |
| `get_note(file_path)` | Read full note by path. Use paths from search results. |
| `get_vault_info()` | Returns vault path, tier, chunk count, installed caps. Use to confirm which vault you're connected to. |
| `get_relevant_context(document_text)` | Find notes related to a block of text. Good for "what else do we know about this?" |
| `list_recent_changes(days)` | Recently modified notes. Good for "what's been worked on?" |

## Score Interpretation

- **0.7+** — Strong match. Auto-load this note.
- **0.5-0.7** — Good match. Auto-load if under 5 notes total.
- **0.3-0.5** — Tangential. Mention but don't auto-load unless nothing better.
- **Below 0.3** — Skip.

## Example Flow

User: "What embedding model do we use?"

1. `search_vault("embedding model")` -> hits on `references/embedding-selection.md` (0.89), `projects/ambient-rag/spec.md` (0.72)
2. `get_note("references/embedding-selection.md")` -> full decision doc
3. `get_note("projects/ambient-rag/spec.md")` -> project spec with model config
4. Answer: "We use Microsoft Harrier 0.6b (1024-dim) for local embeddings and Google text-embedding-005 (768-dim) for AlloyDB. The decision is documented in references/embedding-selection.md — Harrier was chosen for its size/quality tradeoff on Apple Silicon."
