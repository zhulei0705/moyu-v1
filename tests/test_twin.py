import json
import sqlite3
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.twin import TwinService
from backend.knowledge import KnowledgeDB
from backend.observation_store import ObservationStore
from backend.observation_review import ReviewService
from backend.media_service import MediaService
from test_observation import png, META, insert_observation


def setup(tmp_path):
    knowledge=KnowledgeDB(tmp_path/'db.sqlite')
    store=ObservationStore(tmp_path/'db.sqlite',tmp_path/'media')
    return knowledge,store,TwinService(store,knowledge)


def draft(**changes):
    return dict(scene_id='nine-star-research',author='测试研究员',reason='建立隔离研究场景',**changes)


def test_twin_versions_evidence_and_simulator_isolation(tmp_path):
    knowledge,store,service=setup(tmp_path)
    m=MediaService(store).import_bytes(png(),'sim.png',META)
    annotation=ReviewService(store,knowledge).annotate(m['id'],{'annotations':[{'kind':'PLAYER','coordinate_system':'SCREEN_PIXEL','region':[50,50,8,8]}],'author':'标注员','reason':'根据画面可见位置','base_revision':0})
    entry={'data_class':'SIMULATED_ASSUMPTION','evidence_ids':[annotation['id']],'value':annotation['annotations'],'assumption_note':'自建模拟样本标注','execution_environment':'SIMULATOR'}
    body=draft(evidence_ids=[annotation['id']],map_regions=[entry],validation_status='EVIDENCE_LINKED')
    first=service.save(body)
    assert first['configuration']['map_coverage']=='VISIBLE_PORTION_ONLY'
    assert first['configuration']['execution_environment']=='RESEARCH_ONLY'
    assert first['configuration']['current_game_verified'] is False
    with pytest.raises(ValueError):service.save(body)
    second=service.save({**body,'base_revision':1})
    assert second['parent_id']==first['id']
    assert len(service.revisions())==2
    assert store.rows("SELECT * FROM observation_event WHERE event_type='TWIN_SCENE_UPDATED'")
    with pytest.raises(ValueError):service.save(draft(evidence_ids=['missing']))
    with pytest.raises(ValueError):service.save(draft(map_regions=[{'data_class':'SIMULATED_ASSUMPTION','value':'test'}]))
    with pytest.raises(ValueError):service.save(draft(evidence_ids=[annotation['id']],map_regions=[{**entry,'data_class':'OBSERVED_FACT'}]))


def test_real_evidence_rejects_fabricated_mechanics_and_wrong_versions(tmp_path):
    knowledge,store,service=setup(tmp_path)
    m=MediaService(store).import_bytes(png(),'test.png',{**META,'source_type':'GAME_OBSERVATION','game_version':'TEST_FIXTURE','description':'隔离测试夹具，非真实官服样本'})
    insert_observation(store,m['id'],kind='OCR_TEXT',value='画面可见测试值')
    fact=ReviewService(store,knowledge).review('test-result',{'action':'confirm','reviewer':'测试审核员','rationale':'确认测试夹具文字','field':'visible_text'})['fact_id']
    entry={'data_class':'OBSERVED_FACT','evidence_ids':[fact],'value':'画面可见测试值'}
    body=draft(evidence_ids=[fact],visible_mechanics=[entry],game_version='TEST_FIXTURE',validation_status='EVIDENCE_LINKED')
    assert service.save(body)['configuration']['visible_mechanics'][0]['value']=='画面可见测试值'
    with pytest.raises(ValueError):service.save({**body,'base_revision':1,'game_version':None})
    with pytest.raises(ValueError):service.save({**body,'base_revision':1,'visible_mechanics':[{**entry,'value':'未出现的掉落概率'}]})
    with pytest.raises(ValueError):service.save({**body,'base_revision':1,'visible_mechanics':[{**entry,'drop_rate':0.8}]})
    assert knowledge.facts()==[]


def test_official_unknown_and_rollback_protect_history(tmp_path):
    db=tmp_path/'db.sqlite'
    with sqlite3.connect('reports/v12-current-audit/agent.sqlite3') as src,sqlite3.connect(db) as dst:src.backup(dst)
    k=KnowledgeDB(db);s=ObservationStore(db,tmp_path/'media');t=TwinService(s,k)
    facts=k.facts();f=next(f for f in facts if f['verification_status']!='UNKNOWN')
    e=next(e for e in t.evidence() if e['id']==f['id'])
    body=draft(game_version=e['game_version'],evidence_ids=[e['id']],enemy_definitions=[{'data_class':'OFFICIAL_FACT','evidence_ids':[e['id']],'value':e['value']}])
    assert t.save(body)['revision']==1
    unknown=next(f for f in facts if f['verification_status']=='UNKNOWN')
    with pytest.raises(ValueError):t.save(draft(evidence_ids=[unknown['id']]))
    assert k.facts()==facts
    before=s.rows('SELECT * FROM logs ORDER BY id')
    s.db.executescript(Path('backend/migrations/004_twin_down.sql').read_text(encoding='utf-8-sig'))
    assert s.rows('SELECT * FROM logs ORDER BY id')==before
    assert len(s.rows('SELECT * FROM runs'))==8


def test_twin_api_local_control_and_events(tmp_path):
    with TestClient(create_app(tmp_path/'api.sqlite')) as client:
        assert client.post('/api/observations/twins',json=draft()).status_code==403
        headers={'X-Agent-Control':'local-console'}
        assert client.post('/api/observations/twins',json=draft(),headers=headers).status_code==200
        assert len(client.get('/api/observations/twins/nine-star-research').json()['items'])==1
        assert client.get('/api/observations/twins/missing').status_code==404
        assert client.get('/api/observations/twins/evidence').status_code==200
        assert any(e['eventType']=='TWIN_SCENE_UPDATED' for e in client.get('/api/logs').json())
        assert client.post('/api/observations/twins',json=draft(evidence_ids=['missing']),headers=headers).status_code==422

def test_recent_log_query_preserves_history_order(tmp_path):
    with TestClient(create_app(tmp_path/'tail.sqlite')) as client:
        for i in range(12):client.app.state.store.log('fixture','OBSERVATION','INFO','UNKNOWN_RECORDED','测试事件',{'index':i})
        full=client.get('/api/logs?limit=2000').json()
        recent=client.get('/api/logs?tail=true&limit=3').json()
        assert recent==full[-3:]
        assert client.get('/api/logs?after='+str(recent[0]['id'])+'&limit=2000').json()==recent[1:]
        assert len(client.get('/api/logs/export').text.splitlines())==12
