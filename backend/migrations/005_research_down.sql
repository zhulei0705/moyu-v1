-- 必须先备份，先回退006，再回退005；不触碰旧runs/logs/官网事实与观察证据。
DROP TABLE IF EXISTS agent_decision;
DROP TABLE IF EXISTS agent_observation;
DROP TABLE IF EXISTS research_run;
DELETE FROM schema_migration WHERE version=5;
