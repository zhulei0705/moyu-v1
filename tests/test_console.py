import time
from fastapi.testclient import TestClient
from backend.main import create_app

HEADERS={'X-Agent-Control':'local-console'}

def test_idle_metrics_are_unknown_not_synthetic_zero(tmp_path):
    app=create_app(tmp_path/'console.sqlite3')
    with TestClient(app) as client:
        state=client.get('/api/agent/status').json()
        assert state['running'] is False and state['paused'] is False
        dungeon=client.get('/api/dungeon/status').json()
        assert dungeon['loaded'] is False
        for field in ('hp','attack','stage','bag_used','bag_capacity','actions'):
            assert dungeon[field] is None
        bag=client.get('/api/inventory').json()
        assert bag['loaded'] is False and bag['gold'] is None
        assert client.get('/api/earnings').json()['gold']==0 # database-confirmed empty ledger

def test_actual_start_duplicate_and_control_state(tmp_path):
    app=create_app(tmp_path/'console.sqlite3',tick=.1)
    with TestClient(app) as client:
        assert client.post('/api/agent/start',json={'rounds':2},headers=HEADERS).status_code==200
        state=client.get('/api/agent/status').json()
        assert state['running'] and state['runId']
        original=state['runId']
        assert client.post('/api/agent/start',json={},headers=HEADERS).status_code==409
        assert client.get('/api/agent/status').json()['runId']==original
        client.post('/api/agent/pause',headers=HEADERS).raise_for_status()
        assert client.get('/api/agent/status').json()['paused']
        client.post('/api/agent/resume',headers=HEADERS).raise_for_status()
        assert not client.get('/api/agent/status').json()['paused']
        client.post('/api/agent/stop',headers=HEADERS).raise_for_status()
        assert not client.get('/api/agent/status').json()['running']

def test_start_response_is_not_worker_success(tmp_path):
    app=create_app(tmp_path/'console.sqlite3',tick=.001)
    with TestClient(app) as client:
        client.post('/api/agent/start',json={'fault':'crash'},headers=HEADERS).raise_for_status()
        deadline=time.monotonic()+3
        while client.get('/api/agent/status').json()['running'] and time.monotonic()<deadline:
            time.sleep(.01)
        state=client.get('/api/agent/status').json()
        assert not state['running'] and state['error']
        assert client.get('/api/runs').json()[0]['status']=='FAILED'

def test_same_origin_controls_and_restart_preserve_data(tmp_path):
    path=tmp_path/'console.sqlite3'
    app=create_app(path)
    with TestClient(app) as client:
        assert client.post('/api/agent/start',json={},headers={**HEADERS,'Origin':'https://example.com'}).status_code==403
        assert client.get('/api/health').json()['version']=='1.3.3'
    app.state.store.log(None,'IDLE','INFO','audit','保留日志')
    second=create_app(path)
    with TestClient(second) as client:
        assert client.get('/api/logs').json()[0]['message']=='保留日志'
        assert not client.get('/api/agent/status').json()['running']
