-- Restore every referenced PNG inline before downgrading the reader.
-- The CHECK aborts rather than discarding the sole remaining frame copy.
BEGIN IMMEDIATE;
CREATE TEMP TABLE evidence_rollback_guard(ok INTEGER CHECK(ok=1));
INSERT INTO evidence_rollback_guard
 SELECT CASE WHEN COUNT(*)=0 THEN 1 ELSE 0 END FROM agent_observation o
 LEFT JOIN evidence_frame_ref r ON r.observation_id=o.id
 LEFT JOIN evidence_blob b ON b.hash=r.hash
 WHERE length(o.frame_png)=0 AND b.hash IS NULL;
UPDATE agent_observation SET frame_png=(
 SELECT b.png FROM evidence_frame_ref r JOIN evidence_blob b ON b.hash=r.hash
 WHERE r.observation_id=agent_observation.id) WHERE length(frame_png)=0;
DROP TABLE evidence_frame_ref;
DROP TABLE evidence_blob;
DELETE FROM schema_migration WHERE version=8;
DROP TABLE evidence_rollback_guard;
COMMIT;
