CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS note;
CREATE TABLE IF NOT EXISTS note.metadata (
    id SERIAL PRIMARY KEY,
    title TEXT,
    author_id BIGINT
);

CREATE TABLE IF NOT EXISTS note.embedding (
    note_id BIGINT REFERENCES note.metadata(id) PRIMARY KEY,
    embedding VECTOR(384) -- size of output of text-embedding-3-small model 
);

CREATE TABLE IF NOT EXISTS note.page (
    id SERIAL PRIMARY KEY,
    note_id BIGINT REFERENCES note.metadata(id),
    created_at TIMESTAMP,
    content TEXT
);

CREATE TABLE IF NOT EXISTS note.editor (
    note_id BIGINT REFERENCES note.metadata(id),
    editor_id BIGINT,
    PRIMARY KEY(note_id, editor_id)
);