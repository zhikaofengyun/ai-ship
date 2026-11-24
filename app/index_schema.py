"""SQLite schema for versioned text projections."""

SEARCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_search_snapshots(
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, project TEXT NOT NULL, stamp TEXT NOT NULL,
 mode TEXT NOT NULL, space TEXT NOT NULL, watermark INTEGER NOT NULL, source_digest TEXT NOT NULL,
 checked INTEGER NOT NULL, reused INTEGER NOT NULL, based_on TEXT, request_key TEXT NOT NULL,
 UNIQUE(tenant,project,request_key)
);
CREATE TABLE IF NOT EXISTS media_search_heads(
 tenant TEXT NOT NULL, project TEXT NOT NULL, generation TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(tenant,project)
);
CREATE TABLE IF NOT EXISTS media_search_rows(
 row_id INTEGER PRIMARY KEY, generation TEXT NOT NULL REFERENCES media_search_snapshots(id),
 version_id TEXT NOT NULL, pin TEXT NOT NULL, payload TEXT NOT NULL,
 UNIQUE(generation,version_id)
);
CREATE TABLE IF NOT EXISTS media_search_switches(tenant TEXT NOT NULL, project TEXT NOT NULL, revision INTEGER NOT NULL, generation TEXT NOT NULL, actor TEXT NOT NULL, stamp TEXT NOT NULL, PRIMARY KEY(tenant,project,revision));
CREATE VIRTUAL TABLE IF NOT EXISTS media_search_terms USING fts5(terms,tokenize='trigram');
"""
