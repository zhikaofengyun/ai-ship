# Implementation integration contract

Source: contracts/atlas.json extracted without semantic change from provided HTML.
Stack: Python 3.12+, FastAPI + sqlite3 transactional version store, local content-addressed object store, FFmpeg/FFprobe actual media execution; browser ES modules and CSS, no CDN/runtime build dependency.

## API envelope
Successful JSON responses: {data: ..., trace_id: string}. Errors: {error:{code,message,details},trace_id}. All routes /api/v1 unless stated.
GET /api/v1/health: executable/version and runtime mode.
GET /api/v1/catalog: {modules,features,entities,machines,requirements,scenarios,capabilities,counts}; original objects retained.
GET /api/v1/projects: array of immutable fact records (latest).
POST /api/v1/projects: {name,goals,language,market,content_mode,duration,width,height,budget}. Returns Project fact.
GET /api/v1/records?kind=&project_id=&q=: array latest.
POST /api/v1/records: {kind,project_id,data,refs?,reason?}. Returns record.
GET /api/v1/records/{id}?version=: record.
PATCH /api/v1/records/{id}: {expected_revision,data,refs?,reason}; creates immutable next revision.
GET /api/v1/records/{id}/history: array.
Fact record: {id,kind,project_id,tenant_id,version,data,refs,created_at,hash,status}.
GET /api/v1/events?project_id=: array durable events.
GET /api/v1/runs?project_id=: array.
POST /api/v1/commands: full CommandEnvelope from HTML; no fake completion; provider-dependent commands return blocked/unavailable with missing capability and preserve contract.
POST /api/v1/workflows/validate: {nodes:[{id,feature_id,route_id,parameters,optional}],edges:[{source,target,source_port?,target_port?}],max_steps,max_attempts,budget}; returns {valid,errors,order}.
POST /api/v1/parameters/resolve: documented six layers + locks.
POST /api/v1/dependencies/impact: refs/changed fields/ranges; real reference index only, not graph navigation links.

## Media API (separate app/media.py router)
POST /api/v1/media/import?project_id=&filename=: raw file bytes; source metadata via X-Source-Note header optional; returns {asset,version,probe,duplicate,preview_url}. Content hash dedup, save original, bad bytes fail safely.
GET /api/v1/media/{asset_id}/file: range capable original with same-tenant authorization.
POST /api/v1/media/{asset_id}/transform: {project_id,recipe:{...},expected_revision}; actual FFmpeg immutable derivative + provenance.
POST /api/v1/media/render: {project_id,timeline_id,expected_revision,profile:{width,height,fps}} returns persisted RenderJob, run synchronous or asynchronously actual bytes.
GET /api/v1/media/artifacts/{artifact_id}/file
GET /api/v1/media/{asset_id}/waveform
POST /api/v1/media/{asset_id}/extract-audio
Unsupported effects/AI inpainting must explicitly return unavailable, never silently ignored.

## Shared Python store contract
app/store.py exposes Store(db_path), get_store() singleton or app dependency documented by backend agent. Media agent must wait to confirm store method signatures. All mutations atomically append event and version. No storing credentials in record JSON.

## UI routes
Local JS hash routes: #overview, #assets, #editor, #workflow, #agents, #delivery, #insights, #module/{id}, #settings; all 23 modules reachable. Chinese interface. Catalog drives 178 feature inspection and actual parameter configuration persistence, not fictitious executed capabilities. Dedicated production panels for projects, assets, timeline, workflows, agents, publishing, metrics and audit.
Frontend calls API; no fake statistics or fabricated seeded media. Optional clearly labeled sample may be user-triggered only.
