# AmbientRAG

**Make your documents smarter at write time so retrieval actually works at query time.**

AmbientRAG is an enrichment-first retrieval platform. Instead of embedding raw document chunks and hoping vector similarity finds the right one, AmbientRAG enriches every document with persona-aware search metadata, vocabulary bridging, and decision context. Then it indexes that enrichment, not the content. Your documents stay where they are. AmbientRAG makes them findable.

```
Traditional RAG:   raw docs -> chunk -> embed -> pray the query matches
AmbientRAG:        docs -> enrich (10+ search surfaces per doc) -> embed enrichment -> find on first try
```

## The Problem

Every RAG system does the same thing: chunk documents, embed them, search with cosine similarity, stuff results into a prompt. When it doesn't work, they add an agent loop. Multi-pass retrieval, backtracking, re-querying. All of it burning tokens to compensate for dumb documents.

The problem isn't retrieval. It's that raw documents are terrible search targets. A 2,000-token chunk of a security policy is 30% useful signal and 70% boilerplate, headers, and legal disclaimers. The embedding is a blurry average of all of it.

AmbientRAG fixes the documents, not the retrieval.

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

The enrichment gets embedded and indexed. Not the raw document. Every token in the vector is pure search signal. No boilerplate, no filler, no noise. When a query comes in, it matches against a search-optimized representation, not a diluted content chunk.

The original document is retrieved only when the agent needs the full text. Progressive disclosure: summary first (~50 tokens), full document only if needed.

## Where It Came From

AmbientRAG started as a personal problem. I have an Obsidian vault with 1,400+ notes across a dozen projects. Architecture decisions, meeting notes, research, incident postmortems, project specs. Past ~200 notes, `grep` stops working. You know the term but not which note it's in. You remember the decision but not what you called the file.

I built a vector search layer. It helped, but kept returning the wrong notes. Or the right notes ranked below noise. Adding a reranker helped. But the real unlock was enrichment: generating search metadata at write time so retrieval barely has to work. A document that arrives in the index with 10 different ways to be found gets found.

What started as duct-taped fixes kept growing. Each time retrieval failed in a new way, I bolted on another fix. Reranker here, temporal decay there, token caching to reduce my weekly subscription plan. Then I started looking at retrieval optimization and realized some of this could run on an external eGPU. Eventually I looked at the pile and realized each piece could stand on its own:
- **CAP-001** Vector search (pgvector + hybrid BM25)
- **CAP-002** Enrichment pipeline (HyDE questions, persona-aware summaries)
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
| T2 | HyDE enrichment + caveman summaries | ~100-200ms | Smarter documents, fewer missed results |
| T3 | External reranker (eGPU) | ~50-100ms | Maximum precision, dedicated hardware |

## Capabilities

Every enhancement is a numbered Capability (CAP). They form a dependency graph, not a linear sequence.

> **Current release ships CAP-001 (vector search + hybrid BM25).** The remaining capabilities are on the roadmap and will land in future releases. The architecture supports them today, they just aren't packaged yet.

```
CAP-001 (vector search)          <-- the foundation
    |-- CAP-002 (enrichment)
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

## License

Apache 2.0
