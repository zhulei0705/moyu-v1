import asyncio,json,os,threading,uuid,logging,sqlite3,time
from pathlib import Path
from .store import now

POLICY='STOP_UNCONFIRMED_NO_REPLAY'
def migrate(db):
    db.executescript((Path(__file__).parent/'migrations/007_reliability.sql').read_text(encoding='utf-8'))
    db.execute('INSERT OR IGNORE INTO schema_migration VALUES(?,?)',(7,now()));db.commit()

class ServiceHealth:
    def __init__(self,store,runtime):
        self.store,self.runtime=store,runtime
        self.id=os.getenv('MOYU_SERVICE_INSTANCE') or str(uuid.uuid4())
        self.started=None;self.heartbeat=None;self.sse_clients=0
        self.process_started=time.monotonic()
        self.cpu_sample=(time.monotonic(),time.process_time())
        self.cpu_percent=None
    def event(self,kind,details):
        self.store.execute('INSERT INTO service_event(instance_id,event_type,detected_at,details) VALUES(?,?,?,?)',(self.id,kind,now(),json.dumps(details,ensure_ascii=False)))
    def start(self):
        stale=self.store.query("SELECT * FROM service_instance WHERE status='RUNNING'")
        for row in stale:
            self.store.execute("UPDATE service_instance SET status='INTERRUPTED',ended_at=? WHERE id=?",(now(),row['id']))
            self.event('UNCONFIRMED_SERVICE_EXIT',{'previous_instance_id':row['id'],'last_heartbeat':row['heartbeat_at'],'exact_exit_time':None,'reason':'未记录优雅退出；具体原因未知'})
        self.started=self.heartbeat=now()
        self.store.execute('INSERT INTO service_instance VALUES(?,?,?,?,?,?,?)',(self.id,os.getpid(),self.started,self.heartbeat,None,'RUNNING',POLICY))
        self.event('SERVICE_STARTED',{'pid':os.getpid(),'recovery_policy':POLICY})
    async def pulse(self):
        while True:
            timestamp=now()
            self.runtime.stability.detect_stall(self.runtime)
            try:
                await asyncio.to_thread(self.store.execute,'UPDATE service_instance SET heartbeat_at=? WHERE id=?',(timestamp,self.id))
                self.heartbeat=timestamp;self.database_error=None
            except sqlite3.Error as exc:
                self.database_error=str(exc);logging.exception('服务心跳数据库写入失败，将有限间隔重试，不伪造新心跳')
            await asyncio.sleep(1)
    def stopped(self):
        self.store.execute("UPDATE service_instance SET status='STOPPED',ended_at=? WHERE id=?",(now(),self.id));self.event('SERVICE_STOPPED',{'graceful':True})
    def status(self):
        current=(time.monotonic(),time.process_time())
        elapsed=current[0]-self.cpu_sample[0]
        if elapsed>=0.2:
            self.cpu_percent=100*(current[1]-self.cpu_sample[1])/elapsed
            self.cpu_sample=current
        watcher={}
        directory=os.getenv('MOYU_SERVICE_DIR')
        if directory:
            try:watcher=json.loads((Path(directory)/'state.json').read_text(encoding='utf-8'))
            except (OSError,ValueError):pass
        events=self.store.query("SELECT * FROM service_event WHERE event_type='UNCONFIRMED_SERVICE_EXIT' ORDER BY id DESC LIMIT 1")
        recovery=self.store.query('SELECT * FROM agent_recovery ORDER BY reconciled_at DESC LIMIT 20')
        from scripts.service_common import alive,memory_bytes
        supervisor_alive=alive(watcher.get('supervisor_pid')) if directory else None
        return {'instance_id':self.id,'pid':os.getpid(),'service_status':('DEGRADED' if getattr(self,'database_error',None) else 'RUNNING') if self.started else 'NOT_STARTED','database_error':getattr(self,'database_error',None),'started_at':self.started,
          'heartbeat_at':self.heartbeat,'worker':self.runtime.status(),'last_unexpected_exit_detected_at':watcher.get('last_unexpected_exit') or (events[0]['detected_at'] if events else None),
          'exact_previous_exit_time':None,'restart_count':watcher.get('restart_count',0) if directory else None,'watchdog_enabled':watcher.get('watchdog_enabled',False),
          'watchdog_state':watcher.get('state','UNMANAGED') if supervisor_alive is not False else 'SUPERVISOR_UNAVAILABLE','supervisor_alive':supervisor_alive,'max_restarts':watcher.get('max_restarts'),
          'recovery_policy':POLICY,'recovery_status':'INTERRUPTED_REQUIRES_NEW_RUN' if recovery else 'NO_INTERRUPTED_RUNS','reconciled_runs':recovery,
          'sse_clients':self.sse_clients,'thread_count':threading.active_count(),'memory_bytes':memory_bytes(),
          'process_uptime_seconds':current[0]-self.process_started,'cpu_percent_one_core':self.cpu_percent,
          'execution_environment':'SIMULATOR'}
