import hashlib
import json
import sqlite3
import time
import pytest
import asyncio
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.research_scene import SCENE_ID, SceneFailure


def configured(tmp_path):
    path = tmp_path/'agent.sqlite3'
    with sqlite3.connect('reports/v13-baseline/agent.sqlite3') as source, sqlite3.connect(path) as target:
        source.backup(target)
    return create_app(path, tick=.001)


def test_lossless_dedup_references_and_legacy_frames(tmp_path):
    app = configured(tmp_path)
    r = app.state.runtime
    r.start(scene_id=SCENE_ID)
    r.thread.join(30)
    assert r.completed_session == 1
    evidence = r.store.evidence
    observations = r.store.query('SELECT id,frame_sha256 FROM agent_observation')
    with TestClient(app) as client:
        for row in observations:
            frame = evidence.resolve(row['id'])
            assert hashlib.sha256(frame).hexdigest() == row['frame_sha256']
        frame = client.get('/api/research/observations/'+observations[0]['id']+'/frame').content
        assert frame.startswith(b'\x89PNG')
        r.store.execute('UPDATE agent_observation SET frame_png=? WHERE id=?',(frame, observations[0]['id']))
        assert evidence.resolve(observations[0]['id']) == frame
        assert client.get('/api/storage/status').json()['used_bytes'] > 0
    assert r.store.query('SELECT count(*) n FROM evidence_blob')[0]['n'] < len(observations)
    assert r.store.query('PRAGMA foreign_key_check') == []


def test_wal_soft_hard_and_disk_protection(tmp_path, monkeypatch):
    app = configured(tmp_path)
    r = app.state.runtime
    e = r.store.evidence
    status = e.status(force=True)
    r.store.max_database_bytes = int(status['used_bytes']/ .85)
    soft = e.status(force=True)
    assert soft['soft_warning']
    assert soft['debug_capture_interval_seconds'] == 8
    assert soft['files']['-wal'] > 0
    r.store.max_database_bytes = status['used_bytes']-1
    with pytest.raises(SceneFailure): e.guard()
    assert e.last_stop == 'STORAGE_LIMIT'
    r.store.max_database_bytes = 2*1024**3
    import backend.evidence
    disk = type('Disk',(),{'free':0})()
    monkeypatch.setattr(backend.evidence.shutil,'disk_usage',lambda _:disk)
    e.status(force=True)
    with pytest.raises(SceneFailure): e.guard()


def test_debug_batch_frequency_anomaly_archive_failure(tmp_path):
    app = configured(tmp_path)
    e = app.state.store.evidence
    assert not e.debug('DEBUG','filtered')
    for i in range(100):e.debug('INFO','entry',{'n':i})
    assert not e.debug_buffer
    png = b'fake-debug-only'
    assert e.capture_debug(png, stage=1)
    assert not e.capture_debug(b'next', stage=1)
    assert e.capture_debug(b'fault',stage=1,anomaly=True)
    e.flush_debug()
    home = e.path.parent/(e.path.name+'.evidence')
    assert len(list(home.glob('*.png'))) == 3
    before = app.state.store.query('SELECT COUNT(*) n FROM runs')[0]['n']
    archive = tmp_path/'archive.sqlite3'
    assert e.archive(archive)['integrity'] == 'ok'
    assert app.state.store.query('SELECT COUNT(*) n FROM runs')[0]['n'] == before
    with pytest.raises(ValueError):e.archive(archive)
    assert e.last_archive['status'] == 'FAILED'


def test_write_failure_monitor_is_latched(tmp_path):
    app = configured(tmp_path)
    store = app.state.store
    store.db.execute('PRAGMA query_only=ON')
    with pytest.raises(sqlite3.OperationalError):store.execute('INSERT INTO knowledge VALUES(?,?,?,?,?)',('a','b','c','d','{}'))
    status = store.evidence.status(force=True)
    assert status['database_health'] == 'WRITE_ERROR'
    assert status['last_safe_stop'] == 'DATABASE_WRITE_ERROR'


def test_growth_eta_uses_measured_samples(tmp_path):
    app = configured(tmp_path)
    e = app.state.store.evidence
    initial = e.status(force=True)
    e.samples[0] = (time.monotonic()-60,initial['logical_bytes']-6000)
    status = e.status(force=True)
    assert status['bytes_per_minute'] > 0
    assert status['seconds_to_limit'] > 0


def test_sse_large_history_has_no_per_page_delay(tmp_path):
    app = configured(tmp_path)
    store = app.state.store
    first = store.query('SELECT COALESCE(MAX(id),0)+1 n FROM logs')[0]['n']
    with store.lock, store.db:
        store.db.executemany('INSERT INTO logs(timestamp,sequence,runId,stage,level,eventType,message,details) VALUES(?,?,?,?,?,?,?,?)',
            [('now',i,None,'TEST','DEBUG','PRESSURE','test','{}') for i in range(5000)])
    endpoint = next(route.endpoint for route in app.routes if getattr(route,'path',None)=='/api/logs/stream')
    class Request:
        headers = {}
        async def is_disconnected(self):return False
    async def collect_real():
        response = await endpoint(Request(),after=first-1)
        ids=[]; began=time.monotonic()
        iterator=response.body_iterator
        try:
            async for chunk in iterator:
                if chunk.startswith('id: '):ids.append(int(chunk.split('\n')[0][4:]))
                if len(ids)==5000:break
        finally:await iterator.aclose()
        assert ids==list(range(first,first+5000))
        assert time.monotonic()-began<5
    asyncio.run(collect_real())


def test_downgrade_restores_frames_before_old_reader(tmp_path):
    from pathlib import Path
    app = configured(tmp_path)
    r = app.state.runtime
    r.start(scene_id=SCENE_ID);r.thread.join(30)
    assert r.completed_session==1
    rows=r.store.query('SELECT id,frame_sha256 FROM agent_observation')
    with r.store.lock:
        r.store.db.executescript(Path('backend/migrations/008_evidence_down.sql').read_text())
        for row in rows:
            png=r.store.db.execute('SELECT frame_png FROM agent_observation WHERE id=?',(row['id'],)).fetchone()[0]
            assert hashlib.sha256(png).hexdigest()==row['frame_sha256']
    assert r.store.query('PRAGMA integrity_check')[0]['integrity_check']=='ok'


def test_sqlite_failure_preserves_exact_fault_frame_on_filesystem(tmp_path):
    app = configured(tmp_path)
    r = app.state.runtime
    original = r.store.execute
    def fail(sql,args=()):
        if 'INSERT INTO agent_observation' in sql:
            raise sqlite3.OperationalError('injected observation write failure')
        return original(sql,args)
    r.store.execute=fail
    r.start(scene_id=SCENE_ID);r.thread.join(30)
    assert not r.thread.is_alive() and r.completed_session==0
    from pathlib import Path
    png=r.research.last_evidence_frame
    path=Path(r.store.evidence.path.parent)/(r.store.evidence.path.name+'.evidence')/(hashlib.sha256(png).hexdigest()+'.png')
    assert path.read_bytes()==png
    assert r.store.evidence.status(force=True)['database_health']=='WRITE_ERROR'


def test_closed_sqlite_monitor_and_fault_fallback_do_not_need_database(tmp_path):
    app = configured(tmp_path)
    store = app.state.store
    store.db.close()
    assert store.evidence.capture_debug(b'exact-last-sensor',anomaly=True)
    status=store.evidence.status(force=True)
    assert status['database_health']=='WRITE_ERROR'
    assert status['logical_bytes'] is None
    assert status['seconds_to_limit'] is None


def test_hard_threshold_stops_an_active_worker_with_terminal_evidence(tmp_path,monkeypatch):
    app=configured(tmp_path);r=app.state.runtime
    # Inject only the measured capacity dependency after a real observation.
    # File growth/WAL checkpoints and the observer thread cannot race the trigger.
    sampled=[]
    original=r.store.evidence.status
    def status(force=False):
        value=original(force)
        if sampled:value['used_bytes']=r.store.max_database_bytes
        return value
    observe=r.research.observe
    def observed(*args):
        value=observe(*args);sampled.append(args[0]);return value
    monkeypatch.setattr(r.store.evidence,'status',status)
    monkeypatch.setattr(r.research,'observe',observed)
    r.start(100,scene_id=SCENE_ID)
    r.thread.join(15)
    assert not r.thread.is_alive()
    assert r.store.evidence.last_stop=='STORAGE_LIMIT'
    run=r.store.query('SELECT * FROM runs WHERE id=?',(r.run_id,))[0]
    assert sampled and set(sampled)=={r.run_id}
    assert r.status()['stop_reason']=='STORAGE_LIMIT'
    terminal=r.store.query("SELECT details FROM agent_state_history WHERE run_id=? AND state='STOPPED' ORDER BY id DESC LIMIT 1",(r.run_id,))[0]
    assert terminal['details']['reason']=='STORAGE_LIMIT'
    with TestClient(app) as client:
        status=client.get('/api/agent/status').json()
        assert status['runId']==r.run_id and status['stop_reason']=='STORAGE_LIMIT'
        assert client.get('/api/storage/status').json()['last_safe_stop']=='STORAGE_LIMIT'
    from backend.store import Store
    reopened=Store(tmp_path/'agent.sqlite3')
    try:
        assert reopened.evidence.last_stop=='STORAGE_LIMIT'
        saved=reopened.query("SELECT details FROM agent_state_history WHERE run_id=? AND state='STOPPED' ORDER BY id DESC LIMIT 1",(r.run_id,))[0]
        assert saved['details']['reason']=='STORAGE_LIMIT'
    finally:reopened.db.close()
    assert run['status']=='FAILED'
    assert r.store.query('SELECT success FROM research_settlement WHERE run_id=? AND stage_id=0',(r.run_id,))[0]['success']==0
    assert r.store.query('PRAGMA integrity_check')[0]['integrity_check']=='ok'


def test_full_bundle_preserves_media_and_updates_live_monitor(tmp_path):
    from scripts.archive_evidence import archive
    app=configured(tmp_path);store=app.state.store
    media=tmp_path/'observation-media';media.mkdir(exist_ok=True)
    data=b'isolated ordinary diagnostic media'
    (media/'diagnostic.bin').write_bytes(data)
    archive(store.evidence.path,tmp_path/'bundle.zip')
    import zipfile
    with zipfile.ZipFile(tmp_path/'bundle.zip') as bundle:
        assert bundle.read('observation-media/diagnostic.bin')==data
    assert (media/'diagnostic.bin').read_bytes()==data
    assert store.evidence.status(force=True)['last_archive']['status']=='OK'
    with pytest.raises(ValueError):archive(store.evidence.path,tmp_path/'bundle.zip')
    assert store.evidence.status(force=True)['last_archive']['status']=='FAILED'
