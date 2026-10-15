import json
import time
import subprocess
import sys
import sqlite3
import pytest
from fastapi.testclient import TestClient
from backend.world import World
from backend.agent import Agent, astar
from backend.store import Store, now
from backend.runtime import Runtime
from backend.main import create_app


def wait_done(runtime):
    runtime.thread.join(15)
    assert not runtime.thread.is_alive()


@pytest.mark.parametrize('seed', [1,7,42])
def test_visual_closed_loop(seed):
    world, agent = World(seed), Agent()
    rewards = {'gold':0,'shards':0,'materials':0}
    attacks = 0
    for _ in range(500):
        obs = agent.observe(world.render())
        # 真值仅在评估器用于验证视觉精度。
        assert obs.player == world.player
        assert obs.monsters == set(world.monsters)
        assert obs.walls == world.walls
        assert obs.loot == set(world.loot)
        decision = agent.decide(obs)
        events = agent.act(world.act,decision)
        assert agent.verify(obs,agent.observe(world.render()),decision,events)
        for event in events:
            if event['type']=='attack': attacks += 1
            if event['type']=='loot':
                for key in rewards: rewards[key] += event['details']['reward'][key]
        if world.exited: break
    assert world.exited and world.stage==4 and attacks==20
    assert rewards == world.inventory and rewards['materials']==8


def test_astar_obstacles():
    path = astar((0,0),(2,0),{(1,0)},3,3)
    assert len(path)==5 and (1,0) not in path
    assert astar((0,0),(2,0),{(1,0),(0,1)},3,3)==[]


def test_multi_round_and_log_integrity(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    runtime=Runtime(store,0.001)
    runtime.start(3,5)
    wait_done(runtime)
    rows=store.query('SELECT * FROM runs')
    assert len(rows)==3 and all(r['status']=='COMPLETED' for r in rows)
    assert store.earnings()['materials']==24
    for run in rows:
        logs=store.query('SELECT * FROM logs WHERE runId=? ORDER BY id',(run['id'],))
        assert [l['sequence'] for l in logs]==list(range(1,len(logs)+1))
        drops=[l for l in logs if l['eventType']=='loot']
        assert sum(l['details']['reward']['gold'] for l in drops)==run['rewards']['gold']
        assert any(l['eventType']=='settlement' for l in logs)


def test_pause_resume_and_stop(tmp_path):
    runtime=Runtime(Store(tmp_path/'db'),0.02)
    runtime.start(2)
    time.sleep(0.05)
    with pytest.raises(ValueError): runtime.start()
    runtime.control('pause')
    actions=runtime.world.actions
    time.sleep(0.1)
    assert runtime.world.actions==actions
    runtime.control('resume')
    deadline=time.monotonic()+2
    while runtime.world.actions==actions and time.monotonic()<deadline: time.sleep(0.02)
    assert runtime.world.actions>actions
    runtime.control('stop')
    assert not runtime.thread.is_alive() and runtime.state=='STOPPED'
    assert runtime.store.query('SELECT status FROM runs')[0]['status']=='STOPPED'


def test_recovery_failure_emergency_and_restart(tmp_path):
    store=Store(tmp_path/'db')
    runtime=Runtime(store,0.001)
    runtime.start(fault='stuck')
    wait_done(runtime)
    assert store.earnings()['completed']==1
    assert store.query("SELECT * FROM logs WHERE eventType='recover'")
    runtime.start(fault='crash')
    wait_done(runtime)
    assert store.earnings()['failed']==1
    runtime.start()
    runtime.control('emergency-stop')
    assert not runtime.thread.is_alive()
    store.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)',('interrupted','RUNNING',now(),None,None,1,None,None))
    recovered=Store(tmp_path/'db')
    assert recovered.query("SELECT status FROM runs WHERE id='interrupted'")[0]['status']=='INTERRUPTED'


def test_retry_limit():
    world=World()
    agent=Agent()
    obs=agent.observe(world.render())
    decision=agent.decide(obs)
    for _ in range(3): assert not agent.verify(obs,obs,decision,[])
    with pytest.raises(RuntimeError): agent.verify(obs,obs,decision,[])


def test_action_and_run_timeout(tmp_path, monkeypatch):
    original=World.act
    def slow_action(self,*args):
        time.sleep(0.03)
        return original(self,*args)
    monkeypatch.setattr(World,'act',slow_action)
    runtime=Runtime(Store(tmp_path/'action.db'),0.001,action_timeout=0.01)
    runtime.start()
    wait_done(runtime)
    assert runtime.store.query('SELECT * FROM runs')[0]['status']=='FAILED'
    assert runtime.error=='动作超时'
    monkeypatch.setattr(World,'act',original)
    runtime=Runtime(Store(tmp_path/'run.db'),0.001,run_timeout=0)
    runtime.start()
    wait_done(runtime)
    assert runtime.error=='副本运行超时'


def test_pause_excluded_from_timeout(tmp_path):
    runtime=Runtime(Store(tmp_path/'pause.db'),0.01,run_timeout=1)
    runtime.start()
    time.sleep(0.03)
    runtime.control('pause')
    time.sleep(1.05)
    runtime.control('resume')
    time.sleep(0.05)
    assert runtime.error is None and runtime.thread.is_alive()
    runtime.control('stop')


def test_actual_worker_process_restart(tmp_path):
    path=tmp_path/'restart.sqlite'
    code=("from backend.store import Store; from backend.runtime import Runtime; "
          f"r=Runtime(Store({str(path)!r}),0.1); r.start(10); r.thread.join()")
    child_log=tmp_path/'child.log'
    with child_log.open('w',encoding='utf-8') as output:
        process=subprocess.Popen([sys.executable,'-c',code],stdout=output,stderr=output,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        deadline=time.monotonic()+5
        observed=False
        while time.monotonic()<deadline:
            if path.exists():
                try:
                    with sqlite3.connect(path) as db:
                        observed=db.execute("SELECT COUNT(*) FROM logs WHERE eventType='attack'").fetchone()[0]>0
                    if observed: break
                except sqlite3.OperationalError: pass
            time.sleep(0.05)
        assert observed, child_log.read_text(encoding='utf-8',errors='replace') or '真实进程必须先执行攻击动作'
        process.kill()
        process.wait(3)
        store=Store(path)
        assert store.query('SELECT status FROM runs')[0]['status']=='INTERRUPTED'
        assert store.query("SELECT * FROM logs WHERE eventType='interrupted'")
        assert store.earnings()['completed']==0 and store.earnings()['failed']==1
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(3)


def test_api(tmp_path):
    app=create_app(tmp_path/'api.sqlite',0.001)
    with TestClient(app) as client:
        headers={'X-Agent-Control':'local-console'}
        assert client.get('/api/health').status_code==200
        assert client.post('/api/agent/start',json={}).status_code==403
        assert client.post('/api/agent/start',json={},headers={**headers,'Origin':'https://evil.example'}).status_code==403
        assert client.post('/api/agent/resume',headers=headers).status_code==409
        assert client.post('/api/agent/start',json={'rounds':2},headers=headers).status_code==200
        wait_done(app.state.runtime)
        runs=client.get('/api/runs').json()
        assert len(runs)==2 and all(r['status']=='COMPLETED' for r in runs)
        assert client.get('/api/runs/'+runs[0]['id']).json()['events']
        logs=client.get('/api/logs?limit=2000').json()
        exported=[json.loads(l) for l in client.get('/api/logs/export').text.splitlines()]
        assert logs==exported
        assert client.get('/api/frame/latest').content.startswith(b'\x89PNG')
        assert client.get('/api/earnings').json()['materials']==16
        assert client.get('/api/knowledge/map').json()['items']==[]
        assert client.get('/api/agent/status').json()['state']=='STOPPED'
        assert client.get('/api/inventory').json()['source']=='SIMULATED'
        assert client.post('/api/agent/start',headers=headers,json={'rounds':0}).status_code==422
        assert client.post('/api/agent/start',headers=headers,json={'fault':'unknown'}).status_code==422
