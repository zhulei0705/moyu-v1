import pytest
from fastapi.testclient import TestClient
from backend.research_scene import SceneFailure,SCENE_ID
from tests.test_evidence import configured

def test_logical_capacity_stop_latches_monitor(tmp_path,monkeypatch):
    r=configured(tmp_path).state.runtime
    e=r.store.evidence
    r.store.max_database_bytes=1
    monkeypatch.setattr(e,'status',lambda **_: {'used_bytes':0,'disk_free_bytes':e.disk_reserve+1})
    with pytest.raises(SceneFailure,match='容量上限'):r.stability.storage_guard()
    assert e.last_stop=='STORAGE_LIMIT'
    assert 'STORAGE_LIMIT' in e.monitor_file.read_text()

def test_user_stop_is_not_capacity_stop(tmp_path):
    app=configured(tmp_path);r=app.state.runtime;r.tick=1
    r.start(100,scene_id=SCENE_ID);r.control('stop')
    assert not r.thread.is_alive() and r.stop_reason=='USER_STOP'
    assert r.store.evidence.last_stop is None
    with TestClient(app) as client:
        status=client.get('/api/agent/status').json()
        assert status['runId']==r.run_id and status['stop_reason']=='USER_STOP'
    row=r.store.query("SELECT details FROM agent_state_history WHERE run_id=? AND state='STOPPED' ORDER BY id DESC LIMIT 1",(r.run_id,))[0]
    assert row['details']['reason']=='USER_STOP'
