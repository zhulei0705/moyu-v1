CREATE TABLE IF NOT EXISTS research_run(
 run_id TEXT PRIMARY KEY REFERENCES runs(id), scene_id TEXT NOT NULL, dungeon_id TEXT NOT NULL,
 twin_revision_id TEXT NOT NULL REFERENCES twin_scene_revision(id), configuration TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source='SIMULATOR'));
CREATE TABLE IF NOT EXISTS agent_observation(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), stage_id INTEGER NOT NULL,
 created_at TEXT NOT NULL, algorithm_version TEXT NOT NULL, frame_sha256 TEXT NOT NULL,
 frame_png BLOB NOT NULL, perception TEXT NOT NULL, error_code TEXT,
 source TEXT NOT NULL CHECK(source='SIMULATOR'));
CREATE INDEX IF NOT EXISTS agent_observation_run ON agent_observation(run_id,created_at);
CREATE TABLE IF NOT EXISTS agent_decision(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), stage_id INTEGER NOT NULL,
 observation_id TEXT NOT NULL REFERENCES agent_observation(id), after_observation_id TEXT REFERENCES agent_observation(id),
 world_state TEXT NOT NULL, selected_goal TEXT NOT NULL, action_type TEXT NOT NULL,
 decision_reason TEXT NOT NULL, expected_result TEXT NOT NULL, actual_result TEXT NOT NULL,
 decision_latency REAL NOT NULL, error_code TEXT, route TEXT NOT NULL, created_at TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source='SIMULATOR'));
CREATE INDEX IF NOT EXISTS decision_run ON agent_decision(run_id,created_at);
