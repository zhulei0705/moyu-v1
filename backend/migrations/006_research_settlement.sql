CREATE TABLE IF NOT EXISTS research_settlement(
 run_id TEXT NOT NULL REFERENCES research_run(run_id), dungeon_id TEXT NOT NULL,
 stage_id INTEGER NOT NULL CHECK(stage_id BETWEEN 0 AND 4), simulated_gold INTEGER NOT NULL,
 simulated_fragments INTEGER NOT NULL, simulated_materials INTEGER NOT NULL, consumable_cost INTEGER NOT NULL,
 duration REAL NOT NULL, success INTEGER NOT NULL CHECK(success IN (0,1)), settlement_time TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source='SIMULATOR'), cost_unit TEXT NOT NULL CHECK(cost_unit='SIMULATED_SUPPLY_UNIT'),
 PRIMARY KEY(run_id,stage_id));
