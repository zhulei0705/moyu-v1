-- 先归档 twin_scene_revision 再执行；原场景、观察、知识和历史日志不受影响。
DROP TABLE IF EXISTS twin_scene_revision;
DELETE FROM schema_migration WHERE version=4;
