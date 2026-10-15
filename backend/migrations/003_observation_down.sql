-- 仅用于先归档后回退；不触碰 V1.0/V1.1 原表与原日志。
DROP TABLE IF EXISTS observation_event;
DROP TABLE IF EXISTS annotation_revision;
DROP TABLE IF EXISTS observation_conflict;
DROP TABLE IF EXISTS reviewed_observation_fact;
DROP TABLE IF EXISTS observation_review;
DROP TABLE IF EXISTS observation;
DROP TABLE IF EXISTS observation_media;
DELETE FROM schema_migration WHERE version=3;
