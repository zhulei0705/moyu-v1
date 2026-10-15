import json
import sqlite3
import threading
import uuid
from pathlib import Path
from .store import now


def uid(): return str(uuid.uuid4())


def migrate_observation(connection):
    connection.executescript((Path(__file__).parent/'migrations/003_observation.sql').read_text(encoding='utf-8'))
    connection.execute('INSERT OR IGNORE INTO schema_migration VALUES(?,?)',(3,now()))
    connection.commit()


class ObservationStore:
    def __init__(self,path,storage,log_store=None):
        from .db_isolation import validate_path
        path = validate_path(path)
        storage = validate_path(storage)
        self.db=sqlite3.connect(str(path),check_same_thread=False,timeout=20)
        self.db.row_factory=sqlite3.Row
        self.lock=threading.RLock()
        self.storage=Path(storage).resolve()
        self.storage.mkdir(parents=True,exist_ok=True)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=NORMAL')
        migrate_observation(self.db)
        self.log_store=log_store

    def rows(self,sql,args=()):
        with self.lock: rows=[dict(r) for r in self.db.execute(sql,args)]
        for r in rows:
            for field in ('value','region','annotations','corrected_value','details'):
                if field in r and r[field] is not None: r[field]=json.loads(r[field])
        return rows

    def execute(self,sql,args=()):
        with self.lock,self.db: return self.db.execute(sql,args).lastrowid

    def media(self,media_id):
        rows=self.rows('SELECT * FROM observation_media WHERE id=?',(media_id,))
        if not rows: raise ValueError('资料不存在')
        return rows[0]

    def event(self,operation_id,event_type,message,details=None):
        self.execute('INSERT INTO observation_event(operation_id,event_type,details,created_at) VALUES(?,?,?,?)',
                     (operation_id,event_type,json.dumps(details or {},ensure_ascii=False),now()))
        if self.log_store:
            self.log_store.log('observation:'+operation_id,'OBSERVATION','INFO',event_type,message,details)

    def stats(self):
        def count(table,where=''): return self.rows('SELECT COUNT(*) AS n FROM '+table+where)[0]['n']
        return {'materials':count('observation_media',' WHERE parent_id IS NULL'), 'frames':count('observation_media',' WHERE parent_id IS NOT NULL'),
                'game_materials':count('observation_media'," WHERE source_type='GAME_OBSERVATION' AND parent_id IS NULL"),
                'simulator_materials':count('observation_media'," WHERE source_type='SIMULATOR' AND parent_id IS NULL"),
                'observations':count('observation'),'unknown_results':count('observation'," WHERE status='UNKNOWN'"),
                'pending':count('observation'," WHERE status='PENDING'"),'reviewed_facts':count('reviewed_observation_fact'),
                'observed_facts':count('reviewed_observation_fact'," WHERE data_class='OBSERVED_FACT'"),
                'simulated_assumptions':count('reviewed_observation_fact'," WHERE data_class='SIMULATED_ASSUMPTION'"),
                'conflicts':count('observation_conflict'),'recognition_accuracy':None,'map_completeness':None}
