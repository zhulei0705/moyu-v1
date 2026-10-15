PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_migration(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS game_version(
 id TEXT PRIMARY KEY, label TEXT NOT NULL, version_number TEXT, channel TEXT,
 temporal_scope TEXT NOT NULL DEFAULT 'WEB_SNAPSHOT', current_verified INTEGER NOT NULL DEFAULT 0 CHECK(current_verified=0),
 source_type TEXT NOT NULL CHECK(source_type IN ('OFFICIAL_WEB','GAME_OBSERVATION','SIMULATOR','MANUAL_REVIEW')));
CREATE TABLE IF NOT EXISTS data_source(
 id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, source_type TEXT NOT NULL CHECK(source_type IN ('OFFICIAL_WEB','GAME_OBSERVATION','SIMULATOR','MANUAL_REVIEW')),
 published_at TEXT, publication_basis TEXT, url_date_hint TEXT, last_fetched_at TEXT, last_status TEXT, last_error TEXT,
 fetch_count INTEGER NOT NULL DEFAULT 0, robots_url TEXT, robots_sha256 TEXT);
CREATE TABLE IF NOT EXISTS source_snapshot(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES data_source(id), sha256 TEXT NOT NULL,
 fetched_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, http_status INTEGER NOT NULL, encoding TEXT NOT NULL,
 raw_html BLOB NOT NULL, raw_path TEXT NOT NULL, article_text TEXT NOT NULL, article_locator TEXT NOT NULL,
 published_at TEXT, version_id TEXT NOT NULL REFERENCES game_version(id), extractor_version TEXT NOT NULL,
 source_type TEXT NOT NULL CHECK(source_type='OFFICIAL_WEB'), UNIQUE(source_id,sha256));
CREATE TABLE IF NOT EXISTS collection_attempt(
 id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL REFERENCES data_source(id), attempted_at TEXT NOT NULL,
 status TEXT NOT NULL, http_status INTEGER, error TEXT, snapshot_id TEXT REFERENCES source_snapshot(id));
CREATE TABLE IF NOT EXISTS dungeon_definition(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, source_type TEXT NOT NULL CHECK(source_type IN ('OFFICIAL_WEB','GAME_OBSERVATION','SIMULATOR','MANUAL_REVIEW')));
CREATE TABLE IF NOT EXISTS fact_evidence(
 id TEXT PRIMARY KEY, dungeon_id TEXT NOT NULL REFERENCES dungeon_definition(id), entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
 field TEXT NOT NULL, source_type TEXT NOT NULL CHECK(source_type IN ('OFFICIAL_WEB','GAME_OBSERVATION','SIMULATOR','MANUAL_REVIEW')),
 snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id), version_id TEXT NOT NULL REFERENCES game_version(id),
 verification_status TEXT NOT NULL CHECK(verification_status IN ('OFFICIAL_PAGE_EXTRACTED','UNKNOWN','OBSERVED','REVIEWED')),
 raw_value TEXT, normalized_value TEXT NOT NULL, raw_excerpt TEXT NOT NULL, locator TEXT NOT NULL,
 published_at TEXT, collected_at TEXT NOT NULL, parser_version TEXT NOT NULL,
 UNIQUE(snapshot_id,entity_type,entity_id,field));
CREATE INDEX IF NOT EXISTS fact_lookup ON fact_evidence(dungeon_id,entity_type,field);
CREATE TRIGGER IF NOT EXISTS fact_evidence_source_insert BEFORE INSERT ON fact_evidence
WHEN NEW.source_type != (SELECT source_type FROM source_snapshot WHERE id=NEW.snapshot_id)
  OR NEW.version_id != (SELECT version_id FROM source_snapshot WHERE id=NEW.snapshot_id)
BEGIN SELECT RAISE(ABORT,'fact source/version must match snapshot'); END;
CREATE TRIGGER IF NOT EXISTS fact_evidence_source_update BEFORE UPDATE ON fact_evidence
WHEN NEW.source_type != (SELECT source_type FROM source_snapshot WHERE id=NEW.snapshot_id)
  OR NEW.version_id != (SELECT version_id FROM source_snapshot WHERE id=NEW.snapshot_id)
BEGIN SELECT RAISE(ABORT,'fact source/version must match snapshot'); END;
CREATE TABLE IF NOT EXISTS extraction_revision(
 id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id), recorded_at TEXT NOT NULL,
 parser_version TEXT NOT NULL, payload TEXT NOT NULL, source_type TEXT NOT NULL CHECK(source_type='OFFICIAL_WEB'));
CREATE TABLE IF NOT EXISTS dungeon_stage(
 id TEXT PRIMARY KEY, dungeon_id TEXT NOT NULL REFERENCES dungeon_definition(id), snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
 version_id TEXT NOT NULL REFERENCES game_version(id), ordinal INTEGER NOT NULL, name TEXT NOT NULL, boss_count INTEGER,
 source_type TEXT NOT NULL CHECK(source_type='OFFICIAL_WEB'), executable INTEGER NOT NULL DEFAULT 0 CHECK(executable=0));
CREATE TABLE IF NOT EXISTS dungeon_boss(
 id TEXT PRIMARY KEY, dungeon_id TEXT NOT NULL REFERENCES dungeon_definition(id), stage_id TEXT NOT NULL REFERENCES dungeon_stage(id),
 snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id), version_id TEXT NOT NULL REFERENCES game_version(id), name TEXT NOT NULL,
 hp REAL, attack REAL, defense REAL, source_type TEXT NOT NULL CHECK(source_type='OFFICIAL_WEB'));
CREATE TABLE IF NOT EXISTS dungeon_mechanic(
 id TEXT PRIMARY KEY, dungeon_id TEXT NOT NULL REFERENCES dungeon_definition(id), snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
 version_id TEXT NOT NULL REFERENCES game_version(id), name TEXT NOT NULL, description TEXT NOT NULL,
 source_type TEXT NOT NULL CHECK(source_type='OFFICIAL_WEB'), executable INTEGER NOT NULL DEFAULT 0 CHECK(executable=0));
CREATE TABLE IF NOT EXISTS game_item(
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, source_type TEXT NOT NULL CHECK(source_type='OFFICIAL_WEB'));
CREATE TABLE IF NOT EXISTS dungeon_reward(
 id TEXT PRIMARY KEY, dungeon_id TEXT NOT NULL REFERENCES dungeon_definition(id), boss_id TEXT NOT NULL REFERENCES dungeon_boss(id),
 item_id TEXT NOT NULL REFERENCES game_item(id), snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id), version_id TEXT NOT NULL REFERENCES game_version(id),
 quantity REAL, drop_rate REAL, source_type TEXT NOT NULL CHECK(source_type='OFFICIAL_WEB'));
CREATE TABLE IF NOT EXISTS data_conflict(
 id TEXT PRIMARY KEY, dungeon_id TEXT NOT NULL REFERENCES dungeon_definition(id), entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
 field TEXT NOT NULL, evidence_ids TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN', explanation TEXT NOT NULL,
 source_type TEXT NOT NULL CHECK(source_type='OFFICIAL_WEB'));
CREATE TABLE IF NOT EXISTS game_observation(
 id TEXT PRIMARY KEY, dungeon_id TEXT REFERENCES dungeon_definition(id), observed_at TEXT NOT NULL, environment TEXT NOT NULL,
 authorization_reference TEXT NOT NULL, payload TEXT NOT NULL, verification_status TEXT NOT NULL DEFAULT 'UNREVIEWED',
 source_type TEXT NOT NULL CHECK(source_type='GAME_OBSERVATION'));
