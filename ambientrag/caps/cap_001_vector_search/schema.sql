CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS vault_chunks (
    id SERIAL PRIMARY KEY,
    source_path TEXT NOT NULL,
    folder TEXT NOT NULL,
    agent_scope TEXT NOT NULL DEFAULT 'all',
    project TEXT,
    chunk_heading TEXT,
    chunk_text TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    tags TEXT[],
    doc_type TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    embedding_v2 vector(1024),
    embedding_model TEXT DEFAULT 'microsoft/harrier-oss-v1-0.6b',
    embedding_dimension INTEGER DEFAULT 1024,
    embedded_at TIMESTAMPTZ DEFAULT NOW(),
    enriched_summary TEXT,
    hypothetical_questions TEXT[],
    enriched_entities TEXT[],
    enrichment_model TEXT,
    enriched_at TIMESTAMPTZ,
    enrichment_score REAL DEFAULT 0.0,
    enrichment_source TEXT,
    enrichment_reviewed TIMESTAMPTZ,
    tsv tsvector
);

CREATE INDEX IF NOT EXISTS idx_chunks_source     ON vault_chunks(source_path);
CREATE INDEX IF NOT EXISTS idx_chunks_agent_scope ON vault_chunks(agent_scope);
CREATE INDEX IF NOT EXISTS idx_chunks_status      ON vault_chunks(status);
CREATE INDEX IF NOT EXISTS idx_chunks_folder      ON vault_chunks(folder);
CREATE INDEX IF NOT EXISTS idx_chunks_hash        ON vault_chunks(content_hash);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding   ON vault_chunks USING hnsw (embedding_v2 vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_enriched    ON vault_chunks(enriched_at);
CREATE INDEX IF NOT EXISTS idx_chunks_warmness    ON vault_chunks(enrichment_score);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv         ON vault_chunks USING gin(tsv);

CREATE OR REPLACE FUNCTION vault_chunks_tsv_update() RETURNS trigger AS $$
BEGIN
    NEW.tsv := to_tsvector('english', COALESCE(NEW.chunk_text, ''));
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS vault_chunks_tsv_trigger ON vault_chunks;
CREATE TRIGGER vault_chunks_tsv_trigger
    BEFORE INSERT OR UPDATE ON vault_chunks
    FOR EACH ROW EXECUTE FUNCTION vault_chunks_tsv_update();

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS vault_chunks_updated_at ON vault_chunks;
CREATE TRIGGER vault_chunks_updated_at
    BEFORE UPDATE ON vault_chunks
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
