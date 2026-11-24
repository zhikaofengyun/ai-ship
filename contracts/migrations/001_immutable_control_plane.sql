
CREATE TABLE IF NOT EXISTS records(
 tenant_id TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL,
 project_id TEXT, latest_version INTEGER NOT NULL CHECK(latest_version>=1),
 created_at TEXT NOT NULL, PRIMARY KEY(tenant_id,id));
CREATE INDEX IF NOT EXISTS ix_records_scope ON records(tenant_id,kind,project_id,created_at DESC);
CREATE TABLE IF NOT EXISTS revisions(
 tenant_id TEXT NOT NULL, record_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>=1),
 data_json TEXT NOT NULL CHECK(json_valid(data_json)), refs_json TEXT NOT NULL CHECK(json_valid(refs_json)),
 content_hash TEXT NOT NULL, created_at TEXT NOT NULL, actor_id TEXT NOT NULL, reason TEXT NOT NULL,
 PRIMARY KEY(tenant_id,record_id,version),
 FOREIGN KEY(tenant_id,record_id) REFERENCES records(tenant_id,id));
CREATE TABLE IF NOT EXISTS reference_index(
 tenant_id TEXT NOT NULL, source_id TEXT NOT NULL, source_version INTEGER NOT NULL,
 target_id TEXT NOT NULL, target_version INTEGER NOT NULL, field_name TEXT NOT NULL DEFAULT '',
 consumed_fields_json TEXT NOT NULL DEFAULT '[]', range_json TEXT,
 PRIMARY KEY(tenant_id,source_id,source_version,target_id,target_version,field_name),
 FOREIGN KEY(tenant_id,source_id,source_version) REFERENCES revisions(tenant_id,record_id,version),
 FOREIGN KEY(tenant_id,target_id,target_version) REFERENCES revisions(tenant_id,record_id,version));
CREATE INDEX IF NOT EXISTS ix_ref_target ON reference_index(tenant_id,target_id,target_version);
CREATE TABLE IF NOT EXISTS events(
 seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,
 tenant_id TEXT NOT NULL,project_id TEXT,aggregate_id TEXT,aggregate_version INTEGER,
 event_type TEXT NOT NULL,payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 actor_id TEXT NOT NULL,trace_id TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_events_scope ON events(tenant_id,project_id,seq);
CREATE TABLE IF NOT EXISTS command_results(
 tenant_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,command_id TEXT NOT NULL,
 payload_hash TEXT NOT NULL,response_json TEXT NOT NULL CHECK(json_valid(response_json)),
 created_at TEXT NOT NULL,PRIMARY KEY(tenant_id,idempotency_key), UNIQUE(tenant_id,command_id));
CREATE TABLE IF NOT EXISTS leases(
 tenant_id TEXT NOT NULL,resource_id TEXT NOT NULL,owner_id TEXT NOT NULL,
 fencing_token INTEGER NOT NULL CHECK(fencing_token>0),expires_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,resource_id));
CREATE TABLE IF NOT EXISTS consumer_offsets(
 tenant_id TEXT NOT NULL,consumer_id TEXT NOT NULL,last_seq INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(tenant_id,consumer_id));
CREATE TRIGGER IF NOT EXISTS revisions_no_update BEFORE UPDATE ON revisions BEGIN SELECT RAISE(ABORT,'immutable revision'); END;
CREATE TRIGGER IF NOT EXISTS revisions_no_delete BEFORE DELETE ON revisions BEGIN SELECT RAISE(ABORT,'immutable revision'); END;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append-only event'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append-only event'); END;
CREATE TRIGGER IF NOT EXISTS refs_no_update BEFORE UPDATE ON reference_index BEGIN SELECT RAISE(ABORT,'immutable reference'); END;
CREATE TRIGGER IF NOT EXISTS refs_no_delete BEFORE DELETE ON reference_index BEGIN SELECT RAISE(ABORT,'immutable reference'); END;
PRAGMA user_version=1;
