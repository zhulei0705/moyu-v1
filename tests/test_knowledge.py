import hashlib
import json
import sqlite3
from pathlib import Path
import httpx
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from backend.knowledge import KnowledgeDB, key, PARSER_VERSION
from backend.collector import Collector, URLS, parse_page, original_text
from backend.main import create_app

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def pages():
    result={}
    for url in URLS:
        paths=sorted((ROOT/'data'/'official-snapshots').glob(key(url)+'-*.html'))
        assert paths, '缺少真实官网快照，先运行 scripts/collect_official.py'
        result[url]=paths[-1].read_bytes()
    return result


def offline_client(pages):
    def handle(request):
        url=str(request.url)
        if url.endswith('/robots.txt'): return httpx.Response(200,text='User-agent: *\nDisallow:\n')
        return httpx.Response(200,content=pages[url],headers={'content-type':'text/html'})
    return httpx.Client(transport=httpx.MockTransport(handle))


def ingest(tmp_path,pages):
    repo=KnowledgeDB(tmp_path/'knowledge.sqlite')
    collector=Collector(repo,tmp_path/'raw',client=offline_client(pages))
    collector.interval=0 # 离线重放快照测试不发送网络请求。
    result=collector.collect_all()
    assert all(r['status']=='CREATED' for r in result['results']),result
    return repo,collector


@pytest.mark.parametrize('url',URLS)
def test_real_page_structure_and_exact_evidence(pages,url):
    parsed=parse_page(pages[url],url)
    facts={f['field']:f for f in parsed['facts']}
    assert facts['name']['value']=='阿拉玛的哭泣'
    assert facts['boss_total']['value']==7
    assert [s['name'] for s in parsed['stages']]==['烈焰四骑士','莎顿女伯爵','炎魔督军','复仇骑士阿拉玛']
    assert facts['minimum_level']['value']==(50 if '2010-06-02' in url else 90)
    expected=[863,500] if '2015-10-18' in url else [850,500] if '2010-06-02' in url else [864,595]
    assert facts['entry_coordinates']['value']==expected
    assert all(s['rewards'] for s in parsed['stages'])
    for fact in parsed['facts']:
        if fact['value'] is not None:
            node=parsed['soup'].select_one(fact['locator'])
            assert node and original_text(node)==fact['excerpt']
            assert fact['raw_value'] in fact['excerpt']
    assert facts['reward_probabilities']['value'] is None
    assert facts['map_walkability']['value'] is None
    assert facts['minimum_battle_power']['value']==(1100 if '2015-10-18' in url else None)
    assert parsed['published_at']==('2015-10-18' if '2015-10-18' in url else None)


def test_additive_migration_preserves_v10(tmp_path):
    path=tmp_path/'db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE runs(id TEXT, rewards TEXT)')
        db.execute('INSERT INTO runs VALUES(?,?)',('preserved','{"gold":123}'))
        db.execute('CREATE TABLE knowledge(kind TEXT,id TEXT,payload TEXT)')
        db.execute('INSERT INTO knowledge VALUES(?,?,?)',('map','user-existing','{"custom":true}'))
    repo=KnowledgeDB(path)
    assert repo.rows('SELECT * FROM runs')==[{'id':'preserved','rewards':'{"gold":123}'}]
    assert repo.rows('SELECT * FROM knowledge')[0]['id']=='user-existing'
    tables={r['name'] for r in repo.rows("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'game_version','data_source','source_snapshot','dungeon_definition','dungeon_stage','dungeon_boss',
            'dungeon_mechanic','dungeon_reward','game_item','fact_evidence','data_conflict','game_observation'}<=tables
    assert KnowledgeDB(path).rows('SELECT * FROM schema_migration')[0]['version']==2


def test_dedup_conflicts_versions_and_unknowns(tmp_path,pages):
    repo,collector=ingest(tmp_path,pages)
    before=repo.stats()
    result=collector.collect_all()
    assert all(r['status']=='UNCHANGED' for r in result['results'])
    assert repo.stats()==before
    conflicts={c['field']:c for c in repo.conflicts()}
    assert 'minimum_level' in conflicts and 'entry_coordinates' in conflicts
    assert {tuple(f['normalized_value']) for f in conflicts['entry_coordinates']['evidence']}=={(863,500),(864,595),(850,500)}
    assert 'stage_3_rewards' in conflicts
    assert len(repo.detail()['versions'])==3
    assert all(v['version_number'] is None and not v['current_verified'] for v in repo.detail()['versions'])
    assert repo.stats()['current_game_verified_facts']==0
    assert all(r['quantity'] is None and r['drop_rate'] is None for r in repo.entities('rewards'))
    assert all(b['hp'] is None and b['attack'] is None for b in repo.entities('bosses'))
    assert {f['source_type'] for f in repo.facts()}=={'OFFICIAL_WEB'}
    assert all(not s['automatic_execution_allowed'] for s in repo.entities('stages'))


def test_incremental_snapshots_preserve_previous(tmp_path,pages):
    repo,collector=ingest(tmp_path,pages)
    modified=dict(pages)
    # 在真实快照上显式构造测试变更，不作为正式游戏事实写入生产库。
    modified[URLS[0]]=pages[URLS[0]].replace(b'863',b'862')
    collector.client=offline_client(modified)
    result=collector.collect(URLS[0])
    assert result['status']=='CREATED'
    assert repo.stats()['snapshots']==4
    coordinates=[f['normalized_value'] for f in repo.facts('dungeon') if f['field']=='entry_coordinates']
    assert [863,500] in coordinates and [862,500] in coordinates
    assert len(repo.conflicts()[0]['evidence'])>=2


def test_reextract_archives_old_parser_result(tmp_path,pages):
    repo,collector=ingest(tmp_path,pages)
    with repo.db: repo.db.execute("UPDATE source_snapshot SET extractor_version='old-test-parser'")
    assert collector.collect(URLS[0])['status']=='REEXTRACTED'
    assert repo.rows('SELECT * FROM extraction_revision')
    assert len(repo.rows('SELECT id FROM source_snapshot'))==3
    assert all(f['parser_version']==PARSER_VERSION for f in repo.facts())


def test_retry_and_robots_respected(tmp_path,pages):
    counts={'page':0}
    def handler(request):
        if str(request.url).endswith('robots.txt'): return httpx.Response(200,text='User-agent: *\nDisallow:\n')
        counts['page']+=1
        return httpx.Response(503) if counts['page']==1 else httpx.Response(200,content=pages[URLS[0]],headers={'content-type':'text/html'})
    repo=KnowledgeDB(tmp_path/'db')
    collector=Collector(repo,tmp_path/'raw',client=httpx.Client(transport=httpx.MockTransport(handler)))
    collector.interval=0
    assert collector.collect(URLS[0])['status']=='CREATED'
    assert counts['page']==2
    assert [r['status'] for r in repo.rows('SELECT * FROM collection_attempt')]==['FAILED','CREATED']
    blocked=Collector(repo,tmp_path/'raw',client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,text='User-agent: *\nDisallow: /'))))
    blocked.interval=0
    assert blocked.collect(URLS[1])['status']=='FAILED'
    assert len(repo.rows('SELECT id FROM source_snapshot'))==1
    with pytest.raises(ValueError): collector.collect('https://example.com/other-dungeon')


def test_source_types_and_execution_constraints(tmp_path,pages):
    repo,_=ingest(tmp_path,pages)
    with pytest.raises(sqlite3.IntegrityError):
        with repo.db: repo.db.execute("UPDATE dungeon_stage SET executable=1")
    with pytest.raises(sqlite3.IntegrityError):
        with repo.db: repo.db.execute("UPDATE data_source SET source_type='SIMULATED'")
    with pytest.raises(sqlite3.IntegrityError):
        with repo.db: repo.db.execute("UPDATE game_version SET current_verified=1")
    with pytest.raises(sqlite3.IntegrityError):
        with repo.db: repo.db.execute("UPDATE fact_evidence SET source_type='SIMULATOR'")


def test_knowledge_api_restart_and_simulation_isolation(tmp_path,pages):
    repo,_=ingest(tmp_path,pages)
    path=tmp_path/'knowledge.sqlite'
    before=repo.stats()
    app=create_app(path,tick=0.001)
    with TestClient(app) as client:
        for suffix in ('dungeons','dungeons/alama','dungeons/alama/stages','dungeons/alama/bosses','dungeons/alama/rewards','sources','conflicts'):
            response=client.get('/api/knowledge/'+suffix)
            assert response.status_code==200
            assert 'OFFICIAL_WEB' in response.text
        assert client.post('/api/knowledge/dungeons',headers={'X-Agent-Control':'local-console'}).status_code==405
        assert client.get('/api/knowledge/dungeons/other').status_code==404
        assert client.get('/api/knowledge/snapshots/'+repo.rows('SELECT id FROM source_snapshot')[0]['id']).headers['content-type']=='application/octet-stream'
        assert client.get('/api/agent/status').json()['environment']=='SIMULATOR'
        assert client.post('/api/agent/start',json={},headers={'X-Agent-Control':'local-console'}).status_code==200
        app.state.runtime.thread.join(15)
        assert app.state.runtime.completed_session==1
        assert client.get('/api/earnings').json()['source']=='SIMULATED'
        assert app.state.knowledge.stats()==before
        assert client.get('/api/knowledge/map').json()['items']==[]
    restarted=create_app(path)
    with TestClient(restarted) as client:
        assert client.get('/api/knowledge/dungeons').json()['stats']==before
        assert client.get('/api/earnings').json()['completed']==1


def test_live_capture_manifest_and_sha256():
    manifest=json.loads((ROOT/'reports'/'official-collection.json').read_text(encoding='utf-8'))
    assert len(manifest['results'])==3 and all(r['status']!='FAILED' and r['http_status']==200 for r in manifest['results'])
    for r in manifest['results']:
        raw=(ROOT/'data'/'official-snapshots'/f"{key(r['url'])}-{r['sha256']}.html").read_bytes()
        assert hashlib.sha256(raw).hexdigest()==r['sha256']
