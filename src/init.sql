CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    discord_id BIGINT UNIQUE,
    avatar_url TEXT
);

CREATE SCHEMA IF NOT EXISTS note;
CREATE SCHEMA IF NOT EXISTS "role";
CREATE TABLE IF NOT EXISTS role.metadata (
    id SERIAL PRIMARY KEY,
    name TEXT,
    description TEXT
);


CREATE TABLE IF NOT EXISTS note.content (
    id SERIAL PRIMARY KEY,
    title TEXT,
    content TEXT,
    updated_at TIMESTAMP,
    author_id BIGINT REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS note.embedding (
    note_id BIGINT REFERENCES note.content(id),
    model VARCHAR(128),
    embedding VECTOR(384), -- size of output of text-embedding-3-small model 
    PRIMARY KEY(note_id, model)
);

-- available permissions
CREATE TABLE IF NOT EXISTS role.permission (
    id BIGINT PRIMARY KEY,
    key TEXT UNIQUE -- e.g., 'read', 'write', 'delete', 'manage_roles'
);


-- roles
CREATE TABLE IF NOT EXISTS role.role (
  id BIGINT PRIMARY KEY,
  name TEXT,
  description TEXT
);

-- default permissions for a role / for now not important
CREATE TABLE IF NOT EXISTS role.role_permission (
    role_id BIGINT REFERENCES role.role(id),
    permission_id BIGINT REFERENCES role.permission(id),
    allow BOOLEAN, -- allow/deny flag
    PRIMARY KEY(role_id, permission_id)
);

-- resources (shelves, books, chapters, notes) and their hierarchy
CREATE TABLE IF NOT EXISTS role.resource (
    id BIGSERIAL PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('shelf', 'book', 'chapter', 'note')),
    parent_id BIGINT REFERENCES role.resource(id) NULL,
    name TEXT NOT NULL
);

-- assigns permissiont to a role for a specific resource
CREATE TABLE IF NOT EXISTS role.resource_role_permission_assignment (
    id BIGINT PRIMARY KEY,
    permission_id BIGINT REFERENCES role.permission(id),
    role_id BIGINT REFERENCES role.role(id),
    resource_id BIGINT NOT NULL,
    UNIQUE(role_id, resource_id, permission_id)
);

-- assigns a role to a user
CREATE TABLE IF NOT EXISTS role.user_role_assignment (
    id BIGINT PRIMARY KEY,
    user_id BIGINT REFERENCES users(id),
    role_id BIGINT REFERENCES role.role(id),
    UNIQUE(user_id, role_id)
);