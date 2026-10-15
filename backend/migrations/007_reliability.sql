CREATE TABLE IF NOT EXISTS service_instance(
 id TEXT PRIMARY KEY,pid INTEGER NOT NULL,started_at TEXT NOT NULL,heartbeat_at TEXT NOT NULL,
 ended_at TEXT,status TEXT NOT NULL,recovery_policy TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS service_event(
 id INTEGER PRIMARY KEY AUTOINCREMENT,instance_id TEXT,event_type TEXT NOT NULL,
 detected_at TEXT NOT NULL,details TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_state_history(
 id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,state TEXT NOT NULL,changed_at TEXT NOT NULL,details TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS state_run ON agent_state_history(run_id,id);
CREATE TABLE IF NOT EXISTS agent_recovery(
 run_id TEXT PRIMARY KEY,status TEXT NOT NULL,policy TEXT NOT NULL,reconciled_at TEXT NOT NULL,action_replayed INTEGER NOT NULL DEFAULT 0);
