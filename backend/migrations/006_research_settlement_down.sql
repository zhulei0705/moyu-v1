-- 先归档006数据；保留原runs/logs/知识/观察与场景配置。
DROP TABLE IF EXISTS research_settlement;
DELETE FROM schema_migration WHERE version=6;
