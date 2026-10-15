import json
import sqlite3
import threading
from datetime import datetime, timezone

def now(): return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        from .db_isolation import validate_path
        path = validate_path(path)
        self.max_database_bytes = 2 * 1024**3
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=20)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, status TEXT, started TEXT, ended TEXT,
          duration REAL, seed INTEGER, rewards TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
          sequence INTEGER, runId TEXT, stage TEXT, level TEXT, eventType TEXT, message TEXT, details TEXT);
        CREATE INDEX IF NOT EXISTS logs_run ON logs(runId,id);
        CREATE INDEX IF NOT EXISTS logs_sequence ON logs(runId,sequence DESC);
        CREATE TABLE IF NOT EXISTS knowledge(kind TEXT, id TEXT, source TEXT, game_version TEXT,
          payload TEXT, PRIMARY KEY(kind,id));
        ''')
        from .knowledge import migrate
        migrate(self.db)
        from .reliability import migrate as migrate_reliability
        migrate_reliability(self.db)
        from .evidence import Evidence
        self.evidence = Evidence(self, path)
        interrupted = self.query("SELECT id FROM runs WHERE status='RUNNING'")
        self.db.execute("UPDATE runs SET status='INTERRUPTED', ended=?, error='服务重启，运行已中断' WHERE status='RUNNING'", (now(),))
        self.db.commit()
        for run in interrupted:
            self.execute('INSERT OR IGNORE INTO agent_recovery VALUES(?,?,?,?,?)',(run['id'],'INTERRUPTED','STOP_UNCONFIRMED_NO_REPLAY',now(),0))
            self.record_state(run['id'],'INTERRUPTED',{'reason':'服务重启；不重复结果未知的动作','action_replayed':False})
            self.log(run['id'],'STOPPED','ERROR','interrupted','服务重启，遗留运行已标记中断',{'source':'SIMULATED'})

    def record_state(self,run_id,state,details=None):
        self.execute('INSERT INTO agent_state_history(run_id,state,changed_at,details) VALUES(?,?,?,?)',(run_id,state,now(),json.dumps(details or {},ensure_ascii=False)))

    def query(self, sql, args=()):
        with self.lock:
            rows = [dict(r) for r in self.db.execute(sql, args).fetchall()]
        for row in rows:
            for k in ('details','rewards','payload'):
                if k in row and row[k]: row[k] = json.loads(row[k])
        return rows

    def execute(self, sql, args=()):
        with self.lock:
            try:
                cursor = self.db.execute(sql, args)
                self.db.commit()
                self.evidence.write_count += 1
                return cursor.lastrowid
            except sqlite3.Error as exc:
                self.db.rollback()
                self.evidence.write_error = str(exc)
                self.evidence.last_stop = 'DATABASE_WRITE_ERROR'
                self.evidence.persist_monitor()
                raise

    def log(self, run_id, stage, level, event, message, details=None):
        with self.lock:
            sequence = self.db.execute('SELECT COALESCE(MAX(sequence),0) FROM logs WHERE runId IS ?', (run_id,)).fetchone()[0]+1
            return self.execute('INSERT INTO logs(timestamp,sequence,runId,stage,level,eventType,message,details) VALUES(?,?,?,?,?,?,?,?)',
                                (now(),sequence,run_id,stage,level,event,message,json.dumps(details or {},ensure_ascii=False)))

    def earnings(self):
        runs = self.query('SELECT * FROM runs ORDER BY started DESC')
        total = {'gold':0,'shards':0,'materials':0}
        completed = [r for r in runs if r['status']=='COMPLETED']
        for r in completed:
            for key in total: total[key] += (r['rewards'] or {}).get(key,0)
        return dict(source='SIMULATED', **total, completed=len(completed),
                    failed=sum(r['status'] in ('FAILED','INTERRUPTED') for r in runs),
                    averageSeconds=sum(r['duration'] for r in completed)/len(completed) if completed else 0)

    def settle(self, run_id, duration, rewards, actions):
        """成功状态和结算事件使用同一事务提交。"""
        with self.lock, self.db:
            existing=self.db.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()
            if existing and existing[0]=='COMPLETED':return
            if not existing or existing[0]!='RUNNING':raise ValueError('只允许结算已验证的运行任务，不重放中断任务')
            timestamp = now()
            self.db.execute("UPDATE runs SET status='COMPLETED',ended=?,duration=?,rewards=? WHERE id=?",
                            (timestamp,duration,json.dumps(rewards),run_id))
            sequence=self.db.execute('SELECT COUNT(*) FROM logs WHERE runId=?',(run_id,)).fetchone()[0]+1
            self.db.execute('INSERT INTO logs(timestamp,sequence,runId,stage,level,eventType,message,details) VALUES(?,?,?,?,?,?,?,?)',
                            (timestamp,sequence,run_id,'SETTLEMENT','INFO','settlement','真实副本退出后写入 SIMULATED 结算',
                             json.dumps({'rewards':rewards,'actions':actions},ensure_ascii=False)))
