CREATE TABLE IF NOT EXISTS twin_scene_revision(
 id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, revision INTEGER NOT NULL,
 parent_id TEXT REFERENCES twin_scene_revision(id), configuration TEXT NOT NULL,
 author TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(scene_id,revision));
