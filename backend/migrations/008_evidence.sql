-- Additive V1.3.3 migration. No historical rows are rewritten or deleted.
CREATE TABLE IF NOT EXISTS evidence_blob(hash TEXT PRIMARY KEY, png BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_frame_ref(
 observation_id TEXT PRIMARY KEY REFERENCES agent_observation(id),
 hash TEXT NOT NULL REFERENCES evidence_blob(hash));
