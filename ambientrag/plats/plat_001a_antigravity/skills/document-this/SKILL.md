---
name: document-this
description: Capture and save the current conversation to the vault. Use when asked to "document this", "save this", "capture this", "write this down", "dt", "remember this", or "rt". Saves decisions, findings, context, and action items as a structured note.
---

# Document This — Conversation Capture

When the user says "document this", "dt", "save this", "remember this", or "rt", extract the valuable knowledge from the current conversation and save it to the vault inbox. Do NOT ask for confirmation — the trigger phrase IS the confirmation.

## What to Capture

Review the conversation and extract:
1. **Decisions made** — what was decided and why
2. **Key findings** — anything discovered, learned, or confirmed
3. **Context** — the problem or situation that prompted the discussion
4. **Action items** — next steps, todos, follow-ups
5. **Technical details** — code patterns, architecture choices, config worth remembering

Skip: small talk, dead-end debugging, anything the user says to leave out.

## How to Save

Call the `save_note` MCP tool. It handles path discovery, frontmatter, and inbox placement automatically. The intake pipeline files and indexes it.

```
save_note(
  title="Auth middleware decision",
  content="# Auth Middleware Decision\n\n## Context\nWhy this happened...\n\n## Decisions\n- Decision 1\n\n## Key Findings\n- Finding 1\n\n## Action Items\n- [ ] Next step",
  project="ambient-rag",
  tags="auth,middleware,security",
  authored_by="antigravity-session"
)
```

Do NOT use `run_command` or scripts to write files. Do NOT hardcode vault paths. The `save_note` tool knows where the vault is.

### Enrichment is MANDATORY

You are in the conversation. You know what was discussed, what alternatives were rejected, what was decided and why. Use ALL of that context:

- **`hyde_questions`** (5-10): "What would someone search to find this in 6 months?"
- **`hyde_summary`**: One dense line. Max info, min tokens. No articles.
- **`hyde_caveman`**: 2-5 lines of conversation context in caveman speak. What was the discussion about? What was rejected? This is the secret weapon — only you have this context.
- **`related_topics`**: Synonyms and adjacent concepts NOT in the note text
- **`entities`**: Proper nouns — products, people, tools, versions

## After Saving

1. Tell the user the file path
2. The intake pipeline will automatically file it to the correct project folder and index it for search
3. The note becomes searchable via `vault-search` once indexed

## Guidelines

- **Title**: Short and descriptive. "Auth middleware decision" not "Notes from meeting"
- **Be concise**: Capture essence, not transcript. 20-min conversation -> 2-min read
- **Preserve the "why"**: Decisions without rationale are useless in 3 months
- **No confirmation needed**: "document this" IS the go signal. Save immediately.
