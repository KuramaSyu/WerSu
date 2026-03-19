CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY DEFAULT uuidv7()::text,
    discord_id BIGINT UNIQUE NOT NULL,
    avatar TEXT NOT NULL,
    username TEXT NOT NULL,
    discriminator TEXT,
    email TEXT NOT NULL
);

CREATE SCHEMA IF NOT EXISTS note;

CREATE TABLE IF NOT EXISTS note.content (
    id TEXT PRIMARY KEY DEFAULT uuidv7()::text,
    title TEXT,
    content TEXT,
    updated_at TIMESTAMP,
    author_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE,
    search_vector tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', title), 'A') ||  -- title is more important
        setweight(to_tsvector('english', content), 'B')
    ) STORED
);

-- Trigram typo tolerance for content
CREATE INDEX IF NOT EXISTS note_content_title_trgm_idx
ON note.content
USING GIN (title gin_trgm_ops);

-- Trigram typo tolerance for title
CREATE INDEX IF NOT EXISTS idx_note_content_trgm 
ON note.content 
USING GIN (content gin_trgm_ops);

-- Full text search index
CREATE INDEX IF NOT EXISTS note_content_search_idx
ON note.content
USING GIN (search_vector);


CREATE TABLE IF NOT EXISTS note.embedding (
    note_id TEXT NOT NULL REFERENCES note.content(id) ON DELETE CASCADE ON UPDATE CASCADE,
    model VARCHAR(128),
    embedding VECTOR(384), -- size of output of text-embedding-3-small model 
    PRIMARY KEY(note_id, model)
);

CREATE TABLE IF NOT EXISTS note.directory (
    id TEXT PRIMARY KEY DEFAULT uuidv7()::text,
    name TEXT NOT NULL,
    image_url TEXT -- relation to top directory or users stored in spicedb
);