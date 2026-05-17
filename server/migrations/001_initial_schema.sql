-- Memai — initial schema
-- Requires PostgreSQL 15+ and the pgvector extension.
-- Run once against a fresh database:
--   psql -d memai -f migrations/001_initial_schema.sql

CREATE EXTENSION IF NOT EXISTS vector;

-- ─────────────────────────────────────────────────────────────────────────────
-- Users (singleton — one record only)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE users (
    id                  UUID        PRIMARY KEY,
    primary_language    TEXT,                           -- NULL until onboarding
    secondary_languages TEXT[]      NOT NULL DEFAULT '{}'
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Personas (Persona bounded context)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE personas (
    id             UUID        PRIMARY KEY,
    name           TEXT        NOT NULL,
    system_prompt  TEXT        NOT NULL,
    languages      TEXT[]      NOT NULL DEFAULT '{}',  -- empty = primary language only
    is_system      BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Conversations and turns (Memory bounded context — offline only)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE conversations (
    id               BIGSERIAL   PRIMARY KEY,
    started_at       TIMESTAMPTZ NOT NULL,
    ended_at         TIMESTAMPTZ,                      -- NULL while grouping is incomplete
    persona_snapshot JSONB       NOT NULL,             -- AssistantPersona state at conversation start
    worthiness       BOOLEAN,                          -- set by WorthinessEvaluator during consolidation
    summary          TEXT,                             -- set by consolidation
    consolidated     BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE TABLE turns (
    conversation_id BIGINT      NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    session_id      UUID        NOT NULL,              -- source JSONL file; used by TurnLogReplayer for idempotency
    timestamp       TIMESTAMPTZ NOT NULL,
    speaker         TEXT        NOT NULL CHECK (speaker IN ('user', 'assistant')),
    content         TEXT        NOT NULL,
    language        TEXT,                              -- IETF tag, NULL if not detected
    PRIMARY KEY (conversation_id, timestamp)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Memory items (Memory bounded context)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE episodes (
    id                     BIGSERIAL   PRIMARY KEY,
    summary                TEXT        NOT NULL,
    happened_at            TIMESTAMPTZ NOT NULL,
    origin_conversation_id BIGINT      NOT NULL REFERENCES conversations(id),  -- first conversation where this episode was extracted
    embedding              vector(1024)
);

CREATE TABLE concepts (
    id               SERIAL      PRIMARY KEY,
    persona_id       UUID        NOT NULL REFERENCES personas(id) ON DELETE CASCADE,  -- see CLAUDE.md §Data Model
    name             TEXT        NOT NULL,
    description      TEXT        NOT NULL,  -- LLM synthesis ~300 words; see CLAUDE.md
    language         TEXT        NOT NULL,  -- first introduced; fixed on upsert
    engagement_level TEXT        NOT NULL DEFAULT 'mentioned',
    embedding        vector(1024)
);

CREATE TABLE procedures (
    id               SERIAL      PRIMARY KEY,
    persona_id       UUID        NOT NULL REFERENCES personas(id) ON DELETE CASCADE,  -- see CLAUDE.md §Data Model
    name             TEXT        NOT NULL,
    description      TEXT        NOT NULL,  -- LLM synthesis; see CLAUDE.md
    steps            TEXT[]      NOT NULL DEFAULT '{}',  -- empty when not decomposable into discrete steps
    language         TEXT        NOT NULL,  -- first introduced; fixed on upsert
    engagement_level TEXT        NOT NULL DEFAULT 'mentioned',
    embedding        vector(1024)
);

CREATE TABLE memory_brief (
    id           INTEGER     PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- singleton
    content      TEXT        NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Indexes
-- ─────────────────────────────────────────────────────────────────────────────

-- Partial index — the only query pattern on conversations is "give me all
-- unconsolidated ones ordered by started_at".
CREATE INDEX conversations_unconsolidated
    ON conversations (started_at)
    WHERE NOT consolidated;

-- Turn retrieval always scoped to a conversation, ordered by timestamp.
-- The composite PK (conversation_id, timestamp) already covers this pattern.
-- session_id index used by TurnLogReplayer for idempotency check (EXISTS by session).
CREATE INDEX turns_session_id ON turns (session_id);

-- HNSW indexes for vector similarity search (cosine distance).
-- m=16 and ef_construction=64 are pgvector defaults; tune after calibration.
CREATE INDEX episodes_embedding_hnsw
    ON episodes   USING hnsw (embedding vector_cosine_ops);
CREATE INDEX concepts_embedding_hnsw
    ON concepts   USING hnsw (embedding vector_cosine_ops);
CREATE INDEX procedures_embedding_hnsw
    ON procedures USING hnsw (embedding vector_cosine_ops);

-- ─────────────────────────────────────────────────────────────────────────────
-- Seed data
-- ─────────────────────────────────────────────────────────────────────────────

-- GeneralAssistant is the only system persona. It is seeded here so it is
-- always present regardless of application state. The system_prompt can be
-- updated after first launch via voice; the id is fixed and must match
-- GENERAL_ASSISTANT_ID in domain/model.py.
INSERT INTO personas (id, name, system_prompt, languages, is_system, created_at, updated_at)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'General Assistant',
    'You are a helpful, honest assistant.',
    '{}',
    TRUE,
    NOW(),
    NOW()
) ON CONFLICT (id) DO NOTHING;
