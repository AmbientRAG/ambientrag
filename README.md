# AmbientRAG

**Your documents enrich themselves. You just write.**

When your AI assistant saves a note through AmbientRAG, it enriches the document at creation. It already has the conversation context, so it generates persona-aware search metadata, vocabulary bridging, and decision context as part of the write. No separate pipeline, no batch job, no extra tokens. The AI was already there. Enrichment is free.

AmbientRAG indexes both the content and the enrichment, giving every chunk multiple search surfaces. Retrieval works because the documents were ready for it before anyone searched.

```
Traditional RAG:   raw docs -> chunk -> embed -> pray the query matches
AmbientRAG:        docs -> enrich (10+ search surfaces per doc) -> embed -> find on first try
```

## The Problem

Write-time enrichment isn't a new idea. Anthropic's [Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval), [HyPE (Hypothetical Prompt Embeddings)](https://github.com/NirDiamant/RAG_Techniques), and projects like MetaRAG have all shown that enriching documents before indexing dramatically improves recall. The research is clear: spending tokens at write time saves more tokens at query time.

But knowing the technique and having a working system are different things. Most RAG setups still chunk raw documents, embed them, and hope cosine similarity does the work. When it doesn't, they bolt on agent loops. Multi-pass retrieval, backtracking, re-querying. All of it burning tokens to compensate for documents that weren't prepared for search.

AmbientRAG takes the write-time enrichment pattern and packages it into something you can actually run. Persona-aware search surfaces, progressive disclosure, tiered infrastructure, and an MCP server that plugs into any AI assistant. The research proved the concept. This is the product.

## How It Works

Every document gets enriched at write time with multiple search surfaces:

```yaml
# A developer searching "how does login work?" needs to find this auth doc.
# So does the SRE searching "401 errors production" at 3am.
# So does the PM asking "is our auth SOC2 compliant?"
# Raw content matches none of those. Enrichment matches all of them.

hyde_questions:
  - "how does login work?"                    # junior dev vocabulary
  - "where do we validate credentials?"       # senior eng vocabulary
  - "401 errors production auth middleware"    # SRE incident vocabulary
  - "is our auth SOC2 compliant?"             # PM compliance vocabulary
  - "session token TTL configuration"         # code reviewer vocabulary
  - "what caused the March 3 auth outage?"    # incident context

hyde_summary: >-
  Auth middleware. Bcrypt password hash, session token generation,
  rate limited 5 attempts/min. MFA added PR #847 for SOC2.
```

Documents are chunked by heading and indexed with hybrid search: semantic vectors for meaning and keyword search for exact terms. Both rankings are merged so you catch vocabulary mismatches and exact lookups in the same query.

The enrichment metadata rides alongside each chunk, giving the search engine more surface area to match against. A query using different vocabulary than the original note still finds it because the enrichment already bridged that gap at write time.

## Why "Ambient"?

Enrichment happens at the moment of creation, not after.

When you tell your AI assistant to "save a note about this," it already has the full conversation context. It knows what you discussed, what alternatives you rejected, what decision you made and why. AmbientRAG's companion doc (installed via `ambientrag connect`) teaches the assistant to generate enrichment metadata as part of writing the note:

- `hyde_questions`: 5-10 hypothetical search queries from different personas
- `hyde_summary`: A dense one-line summary optimized for embedding
- `hyde_caveman`: A compressed session-context summary for cheap retrieval
- `related_topics`: Semantic synonyms not present in the note text
- `entities`: Proper nouns for exact-match filtering

This is the key difference. The AI assistant was already there when the knowledge was created. It doesn't need a second LLM pass to understand what the note is about. Enrichment at birth is free, contextual, and better than any after-the-fact pipeline could produce.

The enrichment is written into the note's YAML frontmatter. Your content stays untouched. The metadata lives alongside it.

### What about notes the AI didn't write?

Not every note comes through an AI assistant. You type notes by hand in Obsidian. You import docs, paste meeting transcripts, pull in old files from before AmbientRAG existed. Those notes arrive without enrichment.

That's what **CAP-002 (Backfill Enrichment)** is for. A file watcher detects unenriched notes and runs them through an LLM pass to generate the missing metadata. It costs tokens (unlike AI-native enrichment, which is free), but it catches everything the assistant didn't write.

- CAP-001: enrichment at birth (free, the AI was already there)
- CAP-002: enrichment after birth (LLM pass, catches hand-written and imported notes)

### The decision engine

Saving a note manually is fine, but it's not ambient. The real value is when the system decides on its own that something is worth remembering.

AmbientRAG's companion doc includes a decision engine that teaches your AI assistant when to save notes automatically. You never ask. It just happens. Architecture decisions, rejected alternatives, resolved blockers, context that would be expensive to reconstruct later. The assistant recognizes these moments and writes enriched notes to your vault in the background.

The default rules in `~/.ambientrag/decision-engine.yaml`:

```yaml
# When should the assistant automatically save a note?
triggers:
  - type: decision
    description: "A technical or architectural decision was made"
    signal: "user chose X over Y, or rejected an alternative"

  - type: context
    description: "Information that would be hard to reconstruct"
    signal: "debugging findings, root cause analysis, environment-specific details"

  - type: blocker
    description: "A blocker was identified or resolved"
    signal: "something was stuck and now isn't, or a workaround was found"

  - type: rejection
    description: "An approach was considered and rejected"
    signal: "user said no, or an alternative was explored and abandoned"

# Where to save
inbox: agents/{agent}/inbox/

# Enrichment: apply full frontmatter at write time
enrich_on_save: true
```

These are judgment calls, not rigid rules. The assistant reads the conversation and decides if a moment matches a trigger. Some sessions produce five notes. Some produce none.

#### Example: interval-based saving

If you'd rather not rely on judgment and just want regular checkpoints, swap the triggers for a turn counter:

```yaml
# Save/update notes every N conversation turns
mode: interval
interval_turns: 7

# What to capture
capture:
  - decisions made since last save
  - new information learned
  - open questions or blockers

# Update existing notes if the topic hasn't changed
update_existing: true
```

This creates a note every 7 turns, or updates the most recent one if you're still on the same topic. Less nuanced than the decision engine, but predictable.

#### Example: keyword triggers

For teams that want explicit control:

```yaml
mode: keyword
save_keywords: ["TIL", "decision:", "note to self", "remember this"]
```

The assistant only saves when it sees one of these phrases. Everything else is ignored. Maximum control, minimum noise.

The decision engine is the default because it produces the best enrichment. The assistant has full conversation context at the moment it decides to save. But every team works differently. Pick the mode that fits.

### Customizing enrichment

The enrichment rules live in `~/.ambientrag/enrichment.yaml`:

```yaml
# Which files to enrich (CAP-002 backfill)
watch:
  include: ["*.md"]
  exclude: ["_archive/*", "_templates/*"]

# Persona templates for hyde_questions
personas:
  - name: developer
    prompt: "What would a developer search to find this?"
  - name: manager
    prompt: "What would a non-technical manager search to find this?"
  - name: oncall
    prompt: "What would an SRE search during an incident to find this?"

# How many hyde_questions to generate
question_count: 8

# Generate caveman summaries
caveman: true
```

Add a persona, remove one, change the question count, exclude folders. These rules apply to both AI-native enrichment (via the companion doc) and CAP-002 backfill. The defaults work for most vaults, but every setting is yours to adjust.

## Where It Came From

AmbientRAG started as a personal problem. I have an Obsidian vault with 1,400+ notes across a dozen projects. Architecture decisions, meeting notes, research, incident postmortems, project specs. Past ~200 notes, `grep` stops working. You know the term but not which note it's in. You remember the decision but not what you called the file.

I built a vector search layer. It helped, but kept returning the wrong notes. Or the right notes ranked below noise. Adding a reranker helped. But the real unlock was enrichment: generating search metadata at write time so retrieval barely has to work. A document that arrives in the index with 10 different ways to be found gets found.

What started as duct-taped fixes kept growing. Each time retrieval failed in a new way, I bolted on another fix. Reranker here, temporal decay there, token caching to reduce my weekly subscription plan. Then I started looking at retrieval optimization and realized some of this could run on an external eGPU. Eventually I looked at the pile and realized each piece could stand on its own:
- **CAP-001** Vector search + AI-native enrichment (pgvector + hybrid BM25)
- **CAP-002** Backfill enrichment (LLM pass for hand-written and imported notes)
- **CAP-003** Tiered retrieval (progressive disclosure: caveman summary -> chunks -> full doc)
- **CAP-004** Token hygiene (don't re-read what you already know)
- **CAP-005** Temporal scoring (freshness, validity windows, decay)
- **CAP-006** Cross-encoder reranker (precision pass)

Each capability is independent. Install what you need, skip what you don't.

## But Karpathy

In April 2026, Andrej Karpathy published an [idea file](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) for LLM-maintained wikis. The idea: markdown files an LLM compiles and maintains instead of searching raw documents every time. The internet declared RAG dead.

Karpathy himself was more measured. From the gist:

> *"This works surprisingly well at moderate scale (~100 sources, ~hundreds of pages)"*

He's right. For a personal research wiki with 100 articles, you don't need vector search. An `index.md` fits in a context window and the LLM navigates it like a table of contents.

But "moderate scale" is doing a lot of work in that sentence. At 1,000+ documents across a dozen projects (architecture decisions, incident postmortems, meeting notes, research, half-finished specs) the index doesn't fit in context anymore. You need retrieval. The question is whether your retrieval is smart enough to find the right document on the first try.

AmbientRAG isn't anti-wiki. It uses the same curated markdown layer (that's what `hyde_caveman` summaries and `CLAUDE.md` files are). But it adds the vector layer underneath for the long tail. The 1,400 notes that won't fit in any index file. Wiki on top, vectors underneath. Karpathy was the thesis. AmbientRAG is the synthesis.

## The Key Insight

> Everyone else makes agents smarter at query time to compensate for dumb documents.
> AmbientRAG makes documents smarter at write time so retrieval doesn't need compensating.

This is the same relationship as database indexes. You don't run unindexed queries and add retry logic. You build the index once and every future query benefits. Enrichment is the index. The cost is paid at write time. Every search benefits forever.

## Quick Start

```bash
git clone https://github.com/ambientrag/ambientrag.git
cd ambientrag
./install.sh
```

That's it. The install script creates a virtual environment, installs dependencies, runs a health check, and starts you on Tier 0 (SQLite, zero external deps). No Postgres, no Docker, nothing extra to set up.

Once it finishes you'll have a running MCP server with demo notes indexed and searchable from any MCP-capable AI assistant.

Already in a virtual environment? The script detects that and skips venv creation.

Want Postgres later? `ambientrag upgrade --tier 1`

> **Roadmap:** `pip install ambientrag` is planned for a future release. For now, clone and run the install script.

## Tiers

Start at T0. Upgrade when you outgrow it.

| Tier | What You Get | Performance | Use Case |
|------|-------------|-------------|----------|
| **T0** | **SQLite** | **~2-3s** | **Default. Zero deps, just works.** |
| T1 | Postgres + pgvector | ~200-500ms | Hybrid search, daily use |
| T2 | Backfill enrichment + caveman summaries | ~100-200ms | Enrich hand-written notes, fewer missed results |
| T3 | External reranker (eGPU) | ~50-100ms | Maximum precision, dedicated hardware |

## Capabilities

Every enhancement is a numbered Capability (CAP). They form a dependency graph, not a linear sequence.

> **Current release ships CAP-001 (vector search + AI-native enrichment).** The remaining capabilities are on the roadmap and will land in future releases. The architecture supports them today, they just aren't packaged yet.

```
CAP-001 (vector search + AI-native enrichment)   <-- the foundation
    |-- CAP-002 (backfill enrichment)
    |       |-- CAP-003 (tiered retrieval)
    |-- CAP-004 (token hygiene)
    |-- CAP-005 (temporal scoring)
    |-- CAP-006 (reranker)
```

## Why Not Just Use Long Context?

Context windows are 1M+ tokens now. Why not dump everything in?

- **Cost:** Sending 1M tokens per query costs ~$1.50. AmbientRAG's enriched search costs fractions of a penny. Do that 100 times a day.
- **Attention decay:** Models process long contexts unevenly. Middle sections get less attention than beginning/end. Focused 10 pages beats unfocused 100 pages.
- **Signal-to-noise:** Irrelevant documents degrade performance by introducing noise. Retrieval is curation.
- **Speed:** Processing 1M tokens takes 30+ seconds. Enriched search returns in milliseconds.

## Why Not Just Use an Agent Loop?

Agent-powered RAG (search, read, backtrack, re-search) is better than naive RAG. But it's expensive at query time. Think 10-16 tool calls per complex query, reading entire documents each time.

AmbientRAG front-loads that cost to write time. Enrich once, search forever. The agent still decides when to search (it's agentic), but when it does search, the enrichment means it finds the right document on the first try.

## Prior Art

AmbientRAG builds on research and ideas from the community:

- **[HyDE (Hypothetical Document Embeddings)](https://arxiv.org/abs/2212.10496)** and **[HyPE (Hypothetical Prompt Embeddings)](https://github.com/NirDiamant/RAG_Techniques)**: HyDE generates hypothetical answers at query time. HyPE flips it, precomputing hypothetical queries per chunk at index time. AmbientRAG's `hyde_questions` are a persona-bundled implementation of the HyPE pattern.
- **[Anthropic's Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval)**: Write-time chunk enrichment + hybrid search + reranker. Showed 35-67% reduction in retrieval failures. AmbientRAG's CAP pipeline follows a structurally similar approach.
- **[Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)**: LLM-maintained markdown as a retrieval layer. AmbientRAG uses curated summaries (the wiki layer) on top of vectors (the search layer).
- **[LlamaIndex Parent Document Retriever](https://docs.llamaindex.ai/)**: The small-to-big retrieval pattern (summary -> chunks -> full doc) that inspired AmbientRAG's tiered progressive disclosure.

## License

Apache 2.0
