import argparse,datetime,json,os,subprocess,sys,time,uuid,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.service_common import ROOT,directory,runtime_directory,atomic,read,Lease,port_open,health

def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def backoff(attempt,base):return min(30,base*2**(attempt-1))
def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,required=True);p.add_argument('--db',required=True);p.add_argument('--watchdog',action='store_true');p.add_argument('--max-restarts',type=int,default=3);p.add_argument('--base-delay',type=float,default=2);p.add_argument('--startup-timeout',type=float,default=30);p.add_argument('--manager-id',required=True);p.add_argument('--launch-mode',default='DETACHED_CHILD');p.add_argument('--environment',choices=['TEST','SIMULATOR_TEST']);p.add_argument('--test-root');p.add_argument('--source-sha');a=p.parse_args()
    if a.environment:
        if not a.test_root:p.error('explicit test root required')
        os.environ.update(MOYU_ENV=a.environment,MOYU_TEST_ROOT=a.test_root,MOYU_DB=a.db)
        from backend.db_isolation import database_path,test_root
        database_path(a.db)
        temp=test_root()/'process-temp';temp.mkdir(parents=True,exist_ok=True)
        for key in ('TEMP','TMP','TMPDIR'):os.environ[key]=str(temp)
        if a.source_sha:os.environ['MOYU_SOURCE_SHA256']=a.source_sha
    home=directory(a.port);home.mkdir(parents=True,exist_ok=True)
    if a.launch_mode=='WINDOWS_WMI':
        sys.stdout=(home/'manager.log').open('a',encoding='utf-8',buffering=1);sys.stderr=sys.stdout
    db_identity=hashlib.sha256(str(Path(a.db).resolve()).casefold().encode()).hexdigest()
    from backend.db_isolation import database_path
    database_path(a.db)
    with Lease(home/'runner.lock'),Lease(runtime_directory()/('database-'+db_identity+'.lock')):
        old=read(home/'state.json')
        state={'manager_id':a.manager_id,'supervisor_pid':os.getpid(),'server_pid':None,'port':a.port,'host':'127.0.0.1','db':str(Path(a.db).resolve()),'watchdog_enabled':a.watchdog,'max_restarts':a.max_restarts,'restart_count':0,'total_restart_count':old.get('total_restart_count',0),'last_unexpected_exit':old.get('last_unexpected_exit'),'state':'STARTING','manager_started_at':stamp(),'startup_timeout':a.startup_timeout,'launch_mode':a.launch_mode}
        def event(kind,**details):
            with (home/'events.jsonl').open('a',encoding='utf-8') as out:out.write(json.dumps({'timestamp':stamp(),'event':kind,'manager_id':a.manager_id,**details},ensure_ascii=False)+'\n')
        def update(kind,**details):state.update(state=kind,updated_at=stamp(),**details);atomic(home/'state.json',state)
        def stopping():return read(home/'stop.json').get('manager_id')==a.manager_id
        child=None
        try:
            if port_open(a.port):raise RuntimeError('端口已占用，拒绝接管或终止未知服务')
            update('STARTING');event('MANAGER_STARTED',watchdog_enabled=a.watchdog)
            while not stopping():
                instance=str(uuid.uuid4());env=dict(os.environ,MOYU_DB=a.db,MOYU_SERVICE_INSTANCE=instance,MOYU_SERVICE_DIR=str(home),MOYU_PORT=str(a.port),MOYU_MANAGED='1',PYTHONUNBUFFERED='1')
                with (home/'server.log').open('a',encoding='utf-8') as output:
                    child=subprocess.Popen([sys.executable,str(ROOT/'scripts/service_entry.py')],cwd=ROOT,env=env,stdout=output,stderr=output,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    update('STARTING',server_pid=child.pid,launcher_pid=child.pid,instance_id=instance,service_started_at=None)
                    event('SERVER_SPAWNED',pid=child.pid,instance_id=instance)
                    deadline=time.monotonic()+a.startup_timeout;ready=False;timed_out=False
                    while child.poll() is None and not stopping():
                        if not ready:
                            observed=health(a.port)
                            if observed.get('instance_id')==instance:
                                ready=True;update('RUNNING',server_pid=observed['pid'],service_started_at=stamp());event('SERVER_READY',pid=observed['pid'])
                            elif time.monotonic()>deadline:
                                timed_out=True;event('STARTUP_TIMEOUT',pid=child.pid)
                                # Only the owned Popen child can be terminated after a startup deadline.
                                atomic(home/'stop.json',{'manager_id':a.manager_id,'requested_at':stamp(),'intentional':True,'reason':'startup_timeout'})
                                break
                        time.sleep(.2)
                    if stopping():
                        update('STOPPING');event('INTENTIONAL_STOP_REQUESTED')
                        try:child.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            update('STOP_TIMEOUT',error='优雅停止超时；不自动强杀，不再拉起');event('STOP_TIMEOUT');return
                        update('STOPPED',exit_code=child.returncode,stopped_at=stamp());event('INTENTIONAL_STOPPED',exit_code=child.returncode);return
                    code=child.wait(timeout=10);detected=stamp()
                    state.update(last_unexpected_exit=detected,exit_code=code)
                    event('UNEXPECTED_EXIT',pid=child.pid,exit_code=code,startup_timeout=timed_out,exact_cause='UNKNOWN')
                if not a.watchdog or state['restart_count']>=a.max_restarts:
                    update('FAILED',error='服务已退出；Watchdog未启用或已达到重启上限');event('RESTART_DISABLED_OR_EXHAUSTED');return
                state['restart_count']+=1;state['total_restart_count']+=1
                delay=backoff(state['restart_count'],a.base_delay);update('BACKOFF',backoff_seconds=delay);event('RESTART_BACKOFF',attempt=state['restart_count'],seconds=delay)
                deadline=time.monotonic()+delay
                while time.monotonic()<deadline and not stopping():time.sleep(.1)
            update('STOPPED',stopped_at=stamp());event('STOPPED_DURING_BACKOFF')
        except Exception as exc:
            event('MANAGER_ERROR',error=str(exc))
            try:update('FAILED',error=str(exc))
            except OSError:pass # Preserve the primary error and still stop the owned child.
            if child is not None and child.poll() is None:
                # Leave stop intent for service_entry; no PID-based termination of unknown processes.
                atomic(home/'stop.json',{'manager_id':a.manager_id,'requested_at':stamp()})
            raise

if __name__=='__main__':main()
