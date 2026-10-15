PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS observation_media(
 id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, file_type TEXT NOT NULL, filename TEXT NOT NULL, managed_path TEXT NOT NULL,
 preview_path TEXT, byte_size INTEGER NOT NULL, width INTEGER, height INTEGER, duration REAL, parent_id TEXT REFERENCES observation_media(id),
 frame_seconds REAL, captured_at TEXT, imported_at TEXT NOT NULL, game_version TEXT, server_info TEXT, description TEXT NOT NULL,
 source_type TEXT NOT NULL CHECK(source_type IN ('GAME_OBSERVATION','SIMULATOR')), authorization_note TEXT NOT NULL,
 verification_status TEXT NOT NULL DEFAULT 'UNREVIEWED', CHECK(length(authorization_note)>2));
CREATE INDEX IF NOT EXISTS media_sha ON observation_media(sha256);
CREATE TABLE IF NOT EXISTS observation(
 id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES observation_media(id), analysis_id TEXT NOT NULL,
 kind TEXT NOT NULL, raw_text TEXT, value TEXT NOT NULL, region TEXT NOT NULL, coordinate_system TEXT NOT NULL CHECK(coordinate_system='SCREEN_PIXEL'),
 algorithm_version TEXT NOT NULL, confidence REAL CHECK(confidence IS NULL OR (confidence>=0 AND confidence<=1)),
 status TEXT NOT NULL CHECK(status IN ('PENDING','UNKNOWN','CONFIRMED','REJECTED','ERROR')), created_at TEXT NOT NULL, error TEXT);
CREATE TABLE IF NOT EXISTS observation_review(
 id TEXT PRIMARY KEY, observation_id TEXT NOT NULL REFERENCES observation(id), action TEXT NOT NULL,
 reviewer TEXT NOT NULL, rationale TEXT NOT NULL, corrected_value TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviewed_observation_fact(
 id TEXT PRIMARY KEY, observation_id TEXT NOT NULL UNIQUE REFERENCES observation(id), review_id TEXT NOT NULL REFERENCES observation_review(id),
 media_id TEXT NOT NULL REFERENCES observation_media(id), field TEXT NOT NULL, value TEXT NOT NULL, game_version TEXT,
 source_type TEXT NOT NULL CHECK(source_type IN ('GAME_OBSERVATION','SIMULATOR')), data_class TEXT NOT NULL CHECK(data_class IN ('OBSERVED_FACT','SIMULATED_ASSUMPTION')),
 scope TEXT NOT NULL CHECK(scope='VISIBLE_IN_FRAME_ONLY'), status TEXT NOT NULL DEFAULT 'REVIEWED', created_at TEXT NOT NULL,
 CHECK((source_type='SIMULATOR' AND data_class='SIMULATED_ASSUMPTION') OR (source_type='GAME_OBSERVATION' AND data_class='OBSERVED_FACT')));
CREATE TABLE IF NOT EXISTS observation_conflict(
 id TEXT PRIMARY KEY, reviewed_fact_id TEXT NOT NULL REFERENCES reviewed_observation_fact(id), official_fact_id TEXT NOT NULL REFERENCES fact_evidence(id),
 field TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN', explanation TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(reviewed_fact_id,official_fact_id));
CREATE TABLE IF NOT EXISTS annotation_revision(
 id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES observation_media(id), revision INTEGER NOT NULL,
 parent_id TEXT REFERENCES annotation_revision(id), annotations TEXT NOT NULL, author TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(media_id,revision));
CREATE TABLE IF NOT EXISTS observation_event(
 id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL, event_type TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
