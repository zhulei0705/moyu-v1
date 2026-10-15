DROP TABLE IF EXISTS agent_recovery;
DROP TABLE IF EXISTS agent_state_history;
DROP TABLE IF EXISTS service_event;
DROP TABLE IF EXISTS service_instance;
DELETE FROM schema_migration WHERE version=7;
