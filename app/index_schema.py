"""SQLite schema for versioned text projections."""

SEARCH_SCHEMA = """
CREATE INDEX IF NOT EXISTS ix_model_idempotency ON revisions(
    tenant_id,json_extract(data_json,'$.idempotency_scope'))
    WHERE json_extract(data_json,'$.idempotency_scope') IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_team_key ON revisions(tenant_id,json_extract(data_json,'$.team_key'))
    WHERE json_extract(data_json,'$.team_key') IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_model_pending ON revisions(tenant_id,json_extract(data_json,'$.request_semantic_hash'))
    WHERE json_extract(data_json,'$.outcome') IN ('pending','outcome_unknown');
CREATE INDEX IF NOT EXISTS ix_shot_reservation ON revisions(tenant_id,json_extract(data_json,'$.shot_id'))
    WHERE json_extract(data_json,'$.reservation_status')='reserved';
CREATE INDEX IF NOT EXISTS ix_feedback_receipt ON command_results(tenant_id,json_extract(response_json,'$.insight.id'))
    WHERE json_extract(response_json,'$.insight.id') IS NOT NULL;
CREATE TABLE IF NOT EXISTS media_restore_restrictions(
 tenant TEXT NOT NULL, record_id TEXT NOT NULL, kind TEXT NOT NULL, archive_hash TEXT NOT NULL,
 current_hash TEXT, reason TEXT NOT NULL, PRIMARY KEY(tenant,record_id)
);
CREATE TABLE IF NOT EXISTS media_admission_log(
 tenant TEXT NOT NULL, project TEXT NOT NULL, source TEXT NOT NULL, revision INTEGER NOT NULL,
 decision_id TEXT NOT NULL, PRIMARY KEY(tenant,project,source,revision)
);
CREATE TABLE IF NOT EXISTS media_retention_items(
 tenant TEXT NOT NULL, audit TEXT NOT NULL, target TEXT NOT NULL, version INTEGER NOT NULL,
 kind TEXT NOT NULL, disposition TEXT NOT NULL, PRIMARY KEY(tenant,audit,target,version)
);
CREATE INDEX IF NOT EXISTS ix_retention_objects ON revisions(tenant_id,json_extract(data_json,'$.object_key'))
 WHERE json_extract(data_json,'$.object_key') IS NOT NULL;
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
