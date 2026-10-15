import json,os,socket,sqlite3,subprocess,sys,time
from pathlib import Path
import pytest,httpx
from fastapi.testclient import TestClient
from backend.main import create_app
from scripts.service_common import directory,read,alive,atomic
from scripts.service_runner import backoff

ROOT=Path(__file__).resolve().parents[1]
def wait(predicate,seconds=15):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        result=predicate()
        if result:return result
        time.sleep(.15)
    raise AssertionError('等待真实状态超时')
def free_port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]
def command(port,action,*args):
    return subprocess.run([sys.executable,str(ROOT/'scripts/service_manager.py'),action,'--port',str(port),*map(str,args)],cwd=ROOT,capture_output=True,text=True,timeout=50)
@pytest.fixture
def managed(tmp_path):
    port=free_port();db=tmp_path/'service.sqlite3'
    yield port,db
    command(port,'stop')
def test_persistent_state_and_completed_settlement_no_replay(tmp_path):
    path=tmp_path/'state.sqlite3';a=create_app(path,tick=.001);r=a.state.runtime
    r.start();r.thread.join(8);assert not r.thread.is_alive()
    run=a.state.store.query('SELECT * FROM runs')[0];assert run['status']=='COMPLETED'
    states=a.state.store.query('SELECT state FROM agent_state_history WHERE run_id=?',(run['id'],))
    assert {'PREPARE','SEARCH','NAVIGATE','COMBAT','LOOT','SETTLEMENT','STOPPED'}<=set(s['state'] for s in states)
    logs=a.state.store.query('SELECT * FROM logs');a.state.store.settle(run['id'],999,{'gold':999},0)
    assert a.state.store.query('SELECT * FROM logs')==logs
    second=create_app(path);assert second.state.store.query('SELECT * FROM runs')==[run]
    assert second.state.store.earnings()==a.state.store.earnings()
def test_unknown_run_is_interrupted_without_action_replay(tmp_path):
    path=tmp_path/'state.sqlite3';a=create_app(path)
    a.state.store.execute("INSERT INTO runs VALUES('unknown','RUNNING','2026-10-09T00:00:00+00:00',NULL,NULL,1,NULL,NULL)")
    second=create_app(path)
    assert second.state.store.query("SELECT status FROM runs WHERE id='unknown'")[0]['status']=='INTERRUPTED'
    recovery=second.state.store.query('SELECT * FROM agent_recovery')[0]
    assert recovery['action_replayed']==0 and recovery['policy']=='STOP_UNCONFIRMED_NO_REPLAY'
    assert not second.state.runtime.status()['running']
    with pytest.raises(ValueError):second.state.store.settle('unknown',1,{'gold':9},0)
def test_heartbeat_worker_failure_and_lifespan(tmp_path):
    app=create_app(tmp_path/'pulse.sqlite3',tick=.001)
    with TestClient(app) as client:
        first=client.get('/api/service/status').json();time.sleep(1.1);last=client.get('/api/service/status').json()
        assert first['service_status']=='RUNNING' and last['heartbeat_at']>first['heartbeat_at']
        client.post('/api/agent/start',json={'fault':'crash'},headers={'X-Agent-Control':'local-console'})
        wait(lambda:not app.state.runtime.status()['running'])
        assert app.state.store.query("SELECT * FROM service_event WHERE event_type='WORKER_EXCEPTION'")
    assert app.state.store.query('SELECT status FROM service_instance')[0]['status']=='STOPPED'
def test_sqlite_busy_connection_release(tmp_path):
    import threading
    a=create_app(tmp_path/'busy.sqlite3');db=sqlite3.connect(tmp_path/'busy.sqlite3',check_same_thread=False)
    db.execute('BEGIN IMMEDIATE');result=[]
    thread=threading.Thread(target=lambda:result.append(a.state.store.execute('INSERT INTO agent_state_history(state,changed_at,details) VALUES(?,?,?)',('IDLE','test','{}'))))
    thread.start();time.sleep(.2);assert thread.is_alive();db.rollback();thread.join(3);db.close();assert result
def test_backoff_is_exponential_and_capped():
    assert [backoff(i,2) for i in range(1,7)]==[2,4,8,16,30,30]
def test_windows_state_reader_releases_rename_lock(tmp_path):
    import threading
    path=tmp_path/'state.json';atomic(path,{'state':'STARTING'})
    reader=path.open('rb');result=[]
    writer=threading.Thread(target=lambda:(atomic(path,{'state':'RUNNING'}),result.append(True)))
    writer.start();time.sleep(.15);reader.close();writer.join(3)
    assert result and read(path)['state']=='RUNNING'
def test_manager_normal_duplicate_restart_and_stop(managed):
    port,db=managed
    result=command(port,'start','--db',db,'--startup-timeout',15);assert result.returncode==0,result.stderr
    first=read(directory(port)/'state.json');assert not first['watchdog_enabled']
    assert command(port,'start','--db',db).returncode!=0
    assert command(port,'restart','--db',db).returncode==0
    second=read(directory(port)/'state.json');assert second['instance_id']!=first['instance_id']
    assert command(port,'stop').returncode==0
    assert read(directory(port)/'state.json')['state']=='STOPPED'
    time.sleep(.4);assert not alive(second['server_pid'])
def test_watchdog_actual_kill_restart_limit_and_history(managed):
    port,db=managed
    result=command(port,'start','--db',db,'--watchdog','--max-restarts',1,'--base-delay',.2);assert result.returncode==0,result.stderr
    first=read(directory(port)/'state.json');url=f'http://127.0.0.1:{port}'
    httpx.post(url+'/api/agent/start',json={'rounds':3},headers={'X-Agent-Control':'local-console'}).raise_for_status()
    wait(lambda: httpx.get(url+'/api/agent/status').json()['runId'])
    run=httpx.get(url+'/api/agent/status').json()['runId']
    # PID is from verified owned service health, never from a foreign port.
    assert httpx.get(url+'/api/health').json()['instance_id']==first['instance_id']
    subprocess.run(['taskkill','/PID',str(first['server_pid']),'/F'],capture_output=True,check=True)
    wait(lambda:(lambda s:s if s.get('restart_count')==1 and s.get('state')=='RUNNING' else None)(read(directory(port)/'state.json')),20)
    runs=httpx.get(url+'/api/runs').json();assert next(r for r in runs if r['id']==run)['status']=='INTERRUPTED'
    assert not httpx.get(url+'/api/agent/status').json()['running']
    recovery=httpx.get(url+'/api/service/status').json();assert recovery['last_unexpected_exit_detected_at'] and recovery['recovery_status']=='INTERRUPTED_REQUIRES_NEW_RUN'
    second=read(directory(port)/'state.json');subprocess.run(['taskkill','/PID',str(second['server_pid']),'/F'],capture_output=True,check=True)
    wait(lambda:read(directory(port)/'state.json').get('state')=='FAILED')
    assert read(directory(port)/'state.json')['restart_count']==1
    with sqlite3.connect(db) as connection:assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
def test_manual_stop_with_watchdog_never_restarts(managed):
    port,db=managed;assert command(port,'start','--db',db,'--watchdog','--base-delay',.2).returncode==0
    assert command(port,'stop').returncode==0;time.sleep(1)
    s=read(directory(port)/'state.json');assert s['state']=='STOPPED' and s['restart_count']==0 and (directory(port)/'stop.json').exists()
def test_no_watchdog_means_no_auto_restart(managed):
    port,db=managed;assert command(port,'start','--db',db).returncode==0
    s=read(directory(port)/'state.json');subprocess.run(['taskkill','/PID',str(s['server_pid']),'/F'],capture_output=True,check=True)
    wait(lambda:read(directory(port)/'state.json').get('state')=='FAILED');assert read(directory(port)/'state.json')['restart_count']==0
def test_foreign_port_is_never_taken_over(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));sock.listen();port=sock.getsockname()[1]
        assert command(port,'start','--db',tmp_path/'foreign.db').returncode!=0
        assert command(port,'stop').returncode!=0
        assert sock.fileno()>0
def test_same_database_cannot_start_on_another_port(managed):
    port,db=managed;assert command(port,'start','--db',db).returncode==0
    other=free_port()
    assert command(other,'start','--db',db).returncode!=0
    assert httpx.get(f'http://127.0.0.1:{port}/api/health').status_code==200
def test_independent_host_survives_launcher_return_and_stops(managed):
    port,db=managed
    result=command(port,'start','--db',db,'--independent-host');assert result.returncode==0,result.stderr
    time.sleep(.4)
    s=read(directory(port)/'state.json');assert s['launch_mode']=='WINDOWS_WMI' and alive(s['supervisor_pid'])
    assert httpx.get(f'http://127.0.0.1:{port}/api/health').json()['instance_id']==s['instance_id']
    observed=httpx.get(f'http://127.0.0.1:{port}/api/health').json()
    assert observed['execution_environment']=='TEST'
    assert Path(observed['database_path']).resolve()==db.resolve()
    assert Path(os.environ['MOYU_TEST_ROOT']).resolve() in directory(port).resolve().parents
    assert command(port,'stop').returncode==0
def test_dead_metadata_reports_unconfirmed_exit(tmp_path,monkeypatch):
    from scripts import service_manager
    atomic(tmp_path/'state.json',{'state':'RUNNING','manager_id':'expired','supervisor_pid':99999999,'server_pid':99999998})
    monkeypatch.setattr(service_manager,'directory',lambda port:tmp_path)
    monkeypatch.setattr(service_manager,'health',lambda port:{})
    monkeypatch.setattr(service_manager,'port_open',lambda port:False)
    state=service_manager.status(1)
    assert state['state']=='EXITED_UNCONFIRMED' and state['recorded_state']=='RUNNING'
