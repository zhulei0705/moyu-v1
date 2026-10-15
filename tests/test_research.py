import os
import json,sqlite3,time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.research_scene import SCENE_ID,NineStarResearchDungeon,prepare_profile
from backend.research_agent import ResearchAgent
from backend.research_repository import perception
from backend.knowledge import KnowledgeDB
from backend.observation_store import ObservationStore
from backend.twin import TwinService

@pytest.fixture
def app(tmp_path):
    dest=tmp_path/'research.sqlite3'
    with sqlite3.connect('reports/v13-baseline/agent.sqlite3') as src,sqlite3.connect(dest) as db:src.backup(db)
    return create_app(dest,tick=0.001)


def test_profile_single_snapshot_trace_and_assumptions(app):
    r=app.state.runtime;profile=r.profile_factory();config=profile['configuration']
    facts={f['id']:f for f in app.state.knowledge.facts()}
    assert len({facts[i]['snapshot_id'] for i in config['evidence_ids']})==1
    assert all(facts[i]['verification_status']=='OFFICIAL_PAGE_EXTRACTED' for i in config['evidence_ids'])
    assert config['game_version'] is None # 不凭空声称当前官服版本
    assert config['map_regions'][0]['data_class']=='SIMULATED_ASSUMPTION'
    assert config['map_regions'][0]['execution_environment']=='SIMULATOR'
    assert config['visible_mechanics'] and all(m['data_class']=='OFFICIAL_FACT' for m in config['visible_mechanics'])
    assert len(profile['stage_metadata'])==4 and profile['boss_counts'][0]==4
    assert r.profile_factory()['revision_id']==profile['revision_id']
    assert NineStarResearchDungeon(profile).width!=20

@pytest.mark.parametrize('seed',[1,7,42])
def test_nine_star_pixel_astar_complete_four_stages(app,seed):
    profile=app.state.runtime.profile_factory();world=NineStarResearchDungeon(profile,seed);agent=ResearchAgent()
    stages=set();collected={'gold':0,'shards':0,'materials':0};planned=[]
    for i in range(500):
        before=agent.observe(world.render())
        # 此处仅为测试评估器比较环境真值，实际Agent输入仅图像。
        assert before.player==world.player and before.monsters==set(world.monsters)
        assert before.bosses==world.boss_positions and before.inventory==world.inventory
        decision=agent.decide(before);events=agent.act(world.act,decision)
        assert agent.verify(before,agent.observe(world.render()),decision,events)
        if decision.route:planned.append(decision.route)
        if world.stage:stages.add(world.stage)
        for event in events:
            if event['type']=='loot':
                for k in collected:collected[k]+=event['details']['reward'][k]
        if world.exited:break
    assert world.exited and stages=={1,2,3,4} and planned
    assert collected==world.inventory and collected['materials']==11


def test_runtime_nine_star_multi_round_decision_evidence(app):
    runtime=app.state.runtime;runtime.start(2,seed=11,scene_id=SCENE_ID);runtime.thread.join(30)
    assert not runtime.thread.is_alive() and runtime.completed_session==2,runtime.error
    repo=runtime.research;rows=repo.store.query('SELECT r.* FROM runs r JOIN research_run x ON x.run_id=r.id')
    assert len(rows)==2 and all(r['status']=='COMPLETED' for r in rows)
    decisions=repo.decisions(rows[0]['id'],1000)
    assert decisions and all(d['world_state']['basis']=='IMAGE_PIXELS_ONLY' and d['observation_id'] and d['decision_reason'] for d in decisions)
    assert {d['stage_id'] for d in decisions}=={0,1,2,3,4}
    assert any(d['route'] for d in decisions)
    with TestClient(app) as client:
        oid=decisions[0]['observation_id']
        assert client.get('/api/research/observations/'+oid+'/frame').content.startswith(b'\x89PNG')
        assert client.get('/api/research/runs/'+rows[0]['id']).json()['source']=='SIMULATOR'
        assert client.get('/api/research/decisions?run_id='+rows[0]['id']).json()['items']
    (Path(os.environ['MOYU_TEST_ROOT'])/'v13-foundation.json').write_text(json.dumps({'success':True,'rounds':2,'stages':[1,2,3,4],'decision_sample':decisions[0]},ensure_ascii=False,indent=2),encoding='utf-8')
@pytest.mark.parametrize('fault,success,code',[
 ('target_disappeared',True,'TARGET_DISAPPEARED'),('path_blocked',True,'PATH_BLOCKED'),
 ('vision_failed',True,'VISION_FAILED'),('loot_failed',True,'LOOT_FAILED'),('bag_full',True,None),
 ('character_dead',False,'CHARACTER_DEAD'),('vision_persistent',False,'VISION_FAILED'),
 ('stage_timeout',False,'STAGE_TIMEOUT'),('loot_persistent',False,'RECOVERY_EXHAUSTED'),
 ('scene_load_failed',False,'SCENE_LOAD_FAILED'),('worker_interrupted',False,'WORKER_INTERRUPTED')])
def test_recovery_or_bounded_failure(app,fault,success,code):
    r=app.state.runtime;r.start(1,seed=19,fault=fault,scene_id=SCENE_ID);r.thread.join(30)
    assert not r.thread.is_alive(),fault
    row=r.store.query('SELECT * FROM runs WHERE id=?',(r.run_id,))[0]
    assert row['status']==('COMPLETED' if success else 'FAILED'),(fault,row,r.error)
    logs=r.store.query('SELECT * FROM logs WHERE runId=? ORDER BY id',(r.run_id,))
    if code:assert any(e['details'].get('error_code')==code or e['details'].get('error_code')==code for e in logs),(fault,logs[-5:])
    if success:
        assert r.completed_session==1 and r.world.exited
        if fault=='bag_full':assert any(e['eventType']=='deposit' for e in logs) and r.world.consumable_cost>0
    else:
        assert r.completed_session==0 and r.error
        assert len(r.research.decisions(r.run_id,1000))<250
    settlement=r.store.query('SELECT * FROM research_settlement WHERE run_id=? AND stage_id=0',(r.run_id,))[0]
    assert bool(settlement['success'])==success and settlement['source']=='SIMULATOR'
    assert settlement['simulated_gold']==sum(e['details']['reward']['gold'] for e in logs if e['eventType']=='loot')
    assert settlement['consumable_cost']==sum(e['details']['consumable_cost'] for e in logs if e['eventType']=='deposit')


def test_settlement_atomic_and_resource_stage_conservation(app):
    r=app.state.runtime;r.start(2,seed=31,scene_id=SCENE_ID);r.thread.join(30)
    assert r.completed_session==2,r.error
    earnings=r.research.earnings()
    assert earnings['completed']==2 and earnings['success_rate']==1 and earnings['currency_conversion'] is None
    for run in earnings['items']:
        stage=r.store.query('SELECT * FROM research_settlement WHERE run_id=? AND stage_id>0',(run['run_id'],))
        assert len(stage)==4
        assert all(0<s['duration']<run['duration'] and s['success']==1 for s in stage)
        for field in ['simulated_gold','simulated_fragments','simulated_materials','consumable_cost']:
            assert sum(s[field] for s in stage)==run[field]
        logs=r.store.query('SELECT * FROM logs WHERE runId=?',(run['run_id'],))
        assert run['simulated_materials']==sum(e['details']['reward']['materials'] for e in logs if e['eventType']=='loot')==11
        assert run['status']=='COMPLETED' and run['success']==1
    before=r.store.query('SELECT * FROM research_settlement')
    r.research.settle(r.run_id,1,True,r.world)
    assert r.store.query('SELECT * FROM research_settlement')==before


def test_research_control_api_refresh_and_reserved_interfaces(app):
    with TestClient(app) as client:
        h={'X-Agent-Control':'local-console'}
        assert client.get('/api/scenes').json()['items'][1]['id']==SCENE_ID
        assert client.post('/api/agent/start',json={'scene_id':SCENE_ID,'rounds':1},headers=h).status_code==200
        time.sleep(.03)
        assert client.post('/api/agent/pause',headers=h).status_code==200
        state=client.get('/api/agent/status').json();actions=app.state.runtime.world.actions
        time.sleep(.1)
        assert app.state.runtime.world.actions==actions and state['paused'] and state['scene_id']==SCENE_ID
        assert client.get('/api/agent/status').json()['runId']==state['runId']
        assert client.post('/api/agent/resume',headers=h).status_code==200
        assert client.post('/api/agent/emergency-stop',headers=h).status_code==200
        assert client.get('/api/research/earnings').json()['stopped']==1
        assert client.post('/api/research/future-resource-records',headers=h).status_code==501
        assert client.get('/api/research/future-resource-records/schema').json()['required_review']=='MANUAL_REVIEW'
        assert client.post('/api/agent/start',json={'scene_id':'other'},headers=h).status_code==409


def test_real_research_worker_process_interrupt_marks_interrupted(tmp_path):
    import subprocess,sys
    dest=tmp_path/'process.sqlite'
    with sqlite3.connect('reports/v13-baseline/agent.sqlite3') as src,sqlite3.connect(dest) as db:src.backup(db)
    code=f"from backend.main import create_app; from backend.research_scene import SCENE_ID; a=create_app({str(dest)!r},tick=.1); r=a.state.runtime; r.start(4,scene_id=SCENE_ID); r.thread.join()"
    child_log=tmp_path/'child.log'
    with child_log.open('w',encoding='utf-8') as output:
        process=subprocess.Popen([sys.executable,'-c',code],stdout=output,stderr=output,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        deadline=time.monotonic()+12;started=False
        while time.monotonic()<deadline:
            with sqlite3.connect(dest) as db:
                try:started=db.execute("SELECT COUNT(*) FROM logs l JOIN research_run x ON x.run_id=l.runId JOIN runs r ON r.id=x.run_id WHERE l.eventType='attack' AND r.status='RUNNING'").fetchone()[0]>0
                except sqlite3.OperationalError:started=False
            if started:break
            time.sleep(.1)
        assert started, child_log.read_text(encoding='utf-8',errors='replace')
        process.kill();process.wait(4)
        restarted=create_app(dest)
        rows=restarted.state.store.query('SELECT r.* FROM runs r JOIN research_run x ON r.id=x.run_id')
        assert len(rows)==1 and rows[0]['status']=='INTERRUPTED'
        assert restarted.state.runtime.research.earnings()['completed']==0
        assert restarted.state.runtime.research.earnings()['interrupted']==1
    finally:
        if process.poll() is None:process.kill();process.wait(4)


def test_research_migration_rollback_preserves_v12(app):
    db=app.state.store.db
    before=app.state.store.query('SELECT * FROM runs ORDER BY id')
    facts=app.state.knowledge.facts()
    for name in ['008_evidence_down.sql','006_research_settlement_down.sql','005_research_down.sql']:
        db.executescript((Path('backend/migrations')/name).read_text(encoding='utf-8-sig'))
    assert app.state.store.query('SELECT * FROM runs ORDER BY id')==before
    assert app.state.knowledge.facts()==facts
    assert [r['version'] for r in app.state.store.query('SELECT version FROM schema_migration ORDER BY version')]==[2,3,4,7]
