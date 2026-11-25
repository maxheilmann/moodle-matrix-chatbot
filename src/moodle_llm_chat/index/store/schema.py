"""The SQLite schema and its migrations."""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 2


def migrate(con: Any, from_version: int) -> None:
    """Bring an index written by an older build up to :data:`SCHEMA_VERSION`."""
    if from_version < 2:
        columns = {r[1] for r in con.execute("PRAGMA table_info(documents)").fetchall()}
        if "placement_hash" not in columns:
            con.execute("ALTER TABLE documents ADD COLUMN placement_hash TEXT")


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS keys (
    scope TEXT NOT NULL, scope_id TEXT NOT NULL, wrapped BLOB NOT NULL,
    created_at INTEGER NOT NULL, PRIMARY KEY (scope, scope_id)
);

CREATE TABLE IF NOT EXISTS sites (
    site_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    secret_lookup TEXT NOT NULL UNIQUE,
    sealed_secret BLOB NOT NULL,
    prev_secret_lookup TEXT,
    sealed_prev_secret BLOB,
    prev_valid_until INTEGER,
    policy TEXT,
    ceilings TEXT,
    created_at INTEGER NOT NULL,
    revoked_at INTEGER,
    last_seen_at INTEGER
);

CREATE TABLE IF NOT EXISTS courses (
    site_id TEXT NOT NULL, course_id INTEGER NOT NULL,
    payload BLOB NOT NULL,
    visible INTEGER NOT NULL, startdate INTEGER NOT NULL, enddate INTEGER NOT NULL,
    timemodified INTEGER NOT NULL, sync_id TEXT, last_seen INTEGER NOT NULL,
    seen_seq INTEGER NOT NULL DEFAULT 0,
    tombstoned_at INTEGER,
    PRIMARY KEY (site_id, course_id)
);

CREATE TABLE IF NOT EXISTS documents (
    site_id TEXT NOT NULL, doc_id TEXT NOT NULL,
    course_id INTEGER NOT NULL, section_id INTEGER NOT NULL, section_order INTEGER NOT NULL,
    cmid INTEGER NOT NULL, modname TEXT NOT NULL, kind TEXT NOT NULL,
    payload BLOB NOT NULL,
    mimetype TEXT, filesize INTEGER NOT NULL, blob_sha256 TEXT,
    timemodified INTEGER NOT NULL, content_hash TEXT NOT NULL,
    extract_version INTEGER, extracted_at INTEGER, skip_reason TEXT,
    char_count INTEGER NOT NULL DEFAULT 0, page_count INTEGER NOT NULL DEFAULT 0,
    first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, sync_id TEXT,
    seen_seq INTEGER NOT NULL DEFAULT 0,
    tombstoned_at INTEGER,
    placement_hash TEXT,
    PRIMARY KEY (site_id, doc_id)
);
CREATE INDEX IF NOT EXISTS documents_course ON documents(site_id, course_id);
CREATE INDEX IF NOT EXISTS documents_cmid ON documents(site_id, cmid);
CREATE INDEX IF NOT EXISTS documents_extract ON documents(extract_version);

CREATE TABLE IF NOT EXISTS sync_runs (
    site_id TEXT NOT NULL, sync_id TEXT NOT NULL, mode TEXT NOT NULL,
    state TEXT NOT NULL, started_at INTEGER NOT NULL, last_activity_at INTEGER NOT NULL,
    start_seq INTEGER NOT NULL DEFAULT 0,
    finished_at INTEGER,
    PRIMARY KEY (site_id, sync_id)
);

CREATE TABLE IF NOT EXISTS blobs (
    sha256 TEXT PRIMARY KEY, size INTEGER NOT NULL, created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS segments (
    site_id TEXT NOT NULL, doc_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
    course_id INTEGER NOT NULL, page INTEGER, chars INTEGER NOT NULL,
    sealed BLOB NOT NULL,
    PRIMARY KEY (site_id, doc_id, ordinal),
    FOREIGN KEY (site_id, doc_id) REFERENCES documents(site_id, doc_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL, doc_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
    course_id INTEGER NOT NULL, page INTEGER, text_sha256 TEXT NOT NULL,
    sealed BLOB NOT NULL,
    FOREIGN KEY (site_id, doc_id) REFERENCES documents(site_id, doc_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(site_id, doc_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, header_text, content='', contentless_delete=1,
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TABLE IF NOT EXISTS chunk_vectors (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    embedding BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS embedding_cache (
    lookup TEXT NOT NULL, key TEXT NOT NULL, vector BLOB NOT NULL, created_at INTEGER NOT NULL,
    PRIMARY KEY (lookup, key)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL, doc_id TEXT NOT NULL, stage TEXT NOT NULL,
    state TEXT NOT NULL, priority INTEGER NOT NULL, attempts INTEGER NOT NULL,
    next_attempt_at INTEGER NOT NULL, enqueued_at INTEGER NOT NULL,
    started_at INTEGER, finished_at INTEGER, last_error TEXT,
    UNIQUE (site_id, doc_id, stage)
);
CREATE INDEX IF NOT EXISTS jobs_due ON jobs(stage, state, priority, next_attempt_at);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, site_id TEXT NOT NULL, user_id INTEGER NOT NULL,
    course_id INTEGER, sealed_title BLOB, sealed_citations BLOB,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, archived_at INTEGER
);
CREATE INDEX IF NOT EXISTS conversations_owner ON conversations(site_id, user_id, updated_at);

CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    client_msg_id TEXT, state TEXT NOT NULL,
    created_at INTEGER NOT NULL, finished_at INTEGER,
    UNIQUE (conversation_id, client_msg_id)
);
CREATE INDEX IF NOT EXISTS turns_state ON turns(state);

CREATE TABLE IF NOT EXISTS turn_events (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL, turn_id TEXT NOT NULL, sealed BLOB NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (conversation_id, seq)
);
-- The first event seq not yet reserved per conversation (see Store.next_seq).
CREATE TABLE IF NOT EXISTS conversation_seq (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    hi INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ckpt_storage (
    thread_id TEXT NOT NULL, ns TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
    sealed BLOB NOT NULL, PRIMARY KEY (thread_id, ns, checkpoint_id)
);
CREATE TABLE IF NOT EXISTS ckpt_writes (
    thread_id TEXT NOT NULL, ns TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL, idx INTEGER NOT NULL, sealed BLOB NOT NULL,
    PRIMARY KEY (thread_id, ns, checkpoint_id, task_id, idx)
);
CREATE TABLE IF NOT EXISTS ckpt_blobs (
    thread_id TEXT NOT NULL, ns TEXT NOT NULL, channel TEXT NOT NULL, version TEXT NOT NULL,
    sealed BLOB NOT NULL, PRIMARY KEY (thread_id, ns, channel, version)
);

CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL, user_id INTEGER, conversation_id TEXT, turn_id TEXT,
    purpose TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
    reasoning_tokens INTEGER NOT NULL, estimated INTEGER NOT NULL, ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_site_ts ON usage(site_id, purpose, ts);
CREATE INDEX IF NOT EXISTS usage_user_ts ON usage(site_id, user_id, ts);

CREATE TABLE IF NOT EXISTS used_jtis (
    site_id TEXT NOT NULL, jti TEXT NOT NULL, expires_at INTEGER NOT NULL,
    PRIMARY KEY (site_id, jti)
);

CREATE TABLE IF NOT EXISTS ocr_cache (
    lookup TEXT NOT NULL, model_tag TEXT NOT NULL, sealed BLOB NOT NULL,
    created_at INTEGER NOT NULL, PRIMARY KEY (lookup, model_tag)
);
"""
