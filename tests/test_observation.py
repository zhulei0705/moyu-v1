import os
import io
import json
import sqlite3
from pathlib import Path
import cv2
import numpy as np
import pytest
from PIL import Image,ImageDraw,ImageFont
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.world import World
from backend.knowledge import KnowledgeDB
from backend.media_service import MediaService
from backend.observation_store import ObservationStore
from backend.observation_vision import ObservationVision
from backend.observation_review import ReviewService

META={'authorized':True,'source_type':'SIMULATOR','authorization_note':'自建模拟器与原创 OCR 测试图，授权本地研究','description':'测试素材，不是官服截图'}


def setup(tmp_path):
    repo=KnowledgeDB(tmp_path/'db.sqlite')
    store=ObservationStore(tmp_path/'db.sqlite',tmp_path/'media')
    return repo,store,MediaService(store)


def png(world=None):return cv2.imencode('.png',(world or World()).render())[1].tobytes()


def text_image():
    image=Image.new('RGB',(1000,320),'white')
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',36)
    draw=ImageDraw.Draw(image)
    for i,line in enumerate(['卡利亚堡','坐标 850,500','复仇骑士阿拉玛','任务：前往入口','背包：灵魂晶石']):draw.text((30,8+i*60),line,font=font,fill='black')
    buf=io.BytesIO();image.save(buf,format='PNG');return buf.getvalue()


def make_video(path,codec='mp4v'):
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*codec),5,(480,336))
    assert writer.isOpened(),'本机缺少测试视频编码器'
    for i in range(15):
        w=World();w.player=(2+i//5,2);writer.write(w.render())
    writer.release()
    return path.read_bytes()


def test_media_validation_metadata_and_original(tmp_path):
    repo,store,service=setup(tmp_path)
    raw=png();media=service.import_bytes(raw,'../../user.png',META)
    assert media['filename']=='user.png' and media['width']==480 and media['height']==336
    assert media['game_version'] is None and media['server_info'] is None and media['captured_at'] is None
    assert Path(media['managed_path']).read_bytes()==raw
    assert Path(media['preview_path']).exists()
    with pytest.raises(ValueError):service.import_bytes(raw,'bad.exe',META)
    with pytest.raises(ValueError):service.import_bytes(b'<script>bad</script>','bad.png',META)
    with pytest.raises(ValueError):service.import_bytes(raw,'ok.png',{**META,'authorized':False})
    with pytest.raises(ValueError):service.import_bytes(raw,'ok.png',{**META,'authorization_note':''})
    jpeg=cv2.imencode('.jpg',World().render())[1].tobytes()
    assert service.import_bytes(jpeg,'test.jpg',META)['file_type']=='JPG'


@pytest.mark.parametrize('suffix,codec',[('.mp4','mp4v'),('.webm','VP80')])
def test_real_video_frame_extraction_bounded_and_repeatable(tmp_path,suffix,codec):
    repo,store,service=setup(tmp_path)
    raw=make_video(tmp_path/('test'+suffix),codec)
    video=service.import_bytes(raw,'owned'+suffix,META)
    result=service.extract(video['id'],0.5,10)
    assert result['frames'] and len(result['frames'])<=6
    assert result['duplicates_skipped']>0
    for frame in result['frames']:
        assert frame['parent_id']==video['id'] and frame['frame_seconds'] is not None
        assert Path(frame['managed_path']).exists()
    count=store.stats()['frames'];service.extract(video['id'],0.5,10)
    assert store.stats()['frames']==count
    with pytest.raises(ValueError):service.extract(video['id'],0.1,100)


def test_pixel_detection_no_world_truth_and_unknown(tmp_path):
    repo,store,service=setup(tmp_path)
    media=service.import_bytes(png(),'sim.png',META)
    vision=ObservationVision(store,repo)
    # 使用真实 OCR 引擎，渲染画面没有文字则明确 UNKNOWN。
    results=vision.analyze(media['id'])['observations']
    player=[r for r in results if r['kind']=='PLAYER' and r['value'] is not None]
    assert len(player)==1 and player[0]['region']==[50,50,8,8]
    assert player[0]['algorithm_version']=='simulator-pixel-color-v1.2'
    assert all(r['media_id']==media['id'] and r['coordinate_system']=='SCREEN_PIXEL' for r in results)
    assert any(r['kind']=='BOSS_NAME' and r['status']=='UNKNOWN' for r in results)
    assert store.stats()['reviewed_facts']==0


def test_actual_chinese_ocr_link_and_region(tmp_path):
    repo,store,service=setup(tmp_path)
    media=service.import_bytes(text_image(),'original-chinese-test.png',META)
    vision=ObservationVision(store,repo)
    result=vision.analyze(media['id'])
    texts=[r['raw_text'] for r in result['observations'] if r['kind']=='OCR_TEXT' and r['raw_text']]
    combined=''.join(texts)
    assert '卡利亚堡' in combined and '复仇骑士阿拉玛' in combined and '灵魂晶石' in combined,combined
    coordinates=[r for r in result['observations'] if r['kind']=='GAME_COORDINATE' and r['value'] is not None]
    assert coordinates and coordinates[0]['value']==[850,500]
    for r in result['observations']:
        x,y,w,h=r['region'];assert x>=0 and y>=0 and w>0 and h>0 and x+w<=1000 and y+h<=320
        assert r['status']!='CONFIRMED'
    metrics={'sample_source':'SELF_CREATED_TEST_IMAGE','expected_strings':['卡利亚堡','复仇骑士阿拉玛','灵魂晶石'],'recognized':texts,
             'matched_strings':3,'total_strings':3,'game_screen_accuracy':None,'algorithm_version':vision.algorithm}
    (Path(os.environ['MOYU_TEST_ROOT'])/'ocr-accuracy-test.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')


def insert_observation(store,media_id,kind='GAME_COORDINATE',value=None):
    store.execute('INSERT INTO observation VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',('test-result',media_id,'fixture-analysis',kind,'坐标 850,500',
      json.dumps(value),'[10,10,100,30]','SCREEN_PIXEL','TEST_FIXTURE',0.4,'UNKNOWN' if value is None else 'PENDING','test-time',None))


def test_human_correction_source_isolation_and_no_probability(tmp_path):
    repo,store,service=setup(tmp_path)
    media=service.import_bytes(png(),'sim.png',META)
    insert_observation(store,media['id'])
    review=ReviewService(store,repo)
    with pytest.raises(ValueError):review.review('test-result',{'action':'confirm','reviewer':'审核员','rationale':'读取画面坐标','field':'entry_coordinates'})
    body={'action':'correct','reviewer':'审核员','rationale':'人工核对测试图坐标；仅为模拟样本','field':'entry_coordinates','corrected_value':[850,500]}
    result=review.review('test-result',body)
    fact=store.rows('SELECT * FROM reviewed_observation_fact')[0]
    assert fact['data_class']=='SIMULATED_ASSUMPTION' and fact['scope']=='VISIBLE_IN_FRAME_ONLY'
    assert store.rows('SELECT * FROM observation')[0]['value'] is None # 原 OCR 结果保留，纠错另存审核记录。
    assert result['fact_id'] and not result['conflict_ids']
    with pytest.raises(ValueError):review.review('test-result',body)


def test_reviewed_observation_conflict_preserves_official(tmp_path):
    # 仅隔离测试库使用显式测试观察，不计入生产“真实官服资料”。
    source=Path('reports/v1.2-baseline/agent.sqlite3')
    assert source.exists()
    dest=tmp_path/'db.sqlite'
    with sqlite3.connect(source) as old,sqlite3.connect(dest) as db:old.backup(db)
    repo=KnowledgeDB(dest);before=repo.facts();store=ObservationStore(dest,tmp_path/'media')
    media=MediaService(store).import_bytes(text_image(),'test-observation.png',{**META,'source_type':'GAME_OBSERVATION','description':'隔离测试夹具，非官服样本','game_version':'TEST_ONLY'})
    insert_observation(store,media['id'],value=[850,500])
    result=ReviewService(store,repo).review('test-result',{'action':'confirm','field':'entry_coordinates','reviewer':'测试审核员','rationale':'测试画面显示坐标，不能说明当前官服入口'})
    assert len(result['conflict_ids'])==2
    assert repo.facts()==before
    assert store.rows('SELECT * FROM reviewed_observation_fact')[0]['data_class']=='OBSERVED_FACT'


def test_annotation_versions_undo_coordinates_and_coverage(tmp_path):
    repo,store,service=setup(tmp_path)
    media=service.import_bytes(png(),'sim.png',META)
    review=ReviewService(store,repo)
    body={'annotations':[{'kind':'WALKABLE_REGION','coordinate_system':'SCREEN_PIXEL','region':[40,40,20,20]}],'author':'标注员','reason':'只确认可见区域','base_revision':0}
    first=review.annotate(media['id'],body)
    assert first['revision']==1 and first['annotations'][0]['coverage']=='VISIBLE_PORTION_ONLY'
    with pytest.raises(ValueError):review.annotate(media['id'],body)
    second=review.annotate(media['id'],{**body,'annotations':[],'base_revision':1,'reason':'撤销上次标注，保留旧版本'})
    assert second['parent_id']==first['id'] and len(store.rows('SELECT * FROM annotation_revision'))==2
    with pytest.raises(ValueError):review.annotate(media['id'],{**body,'base_revision':2,'annotations':[{'kind':'PLAYER','coordinate_system':'GAME_COORDINATE','region':[1,1,2,2]}]})
    with pytest.raises(ValueError):review.annotate(media['id'],{**body,'base_revision':2,'annotations':[{'kind':'OBSTACLE','coordinate_system':'SCREEN_PIXEL','region':[470,330,50,50]}]})


def test_upload_api_auth_preview_and_events(tmp_path):
    app=create_app(tmp_path/'api.db')
    with TestClient(app) as client:
        headers={'X-Agent-Control':'local-console'}
        assert client.post('/api/observations/media',files={'file':('sim.png',png())},data={'metadata':json.dumps(META)}).status_code==403
        response=client.post('/api/observations/media',files={'file':('sim.png',png())},data={'metadata':json.dumps(META)},headers=headers)
        assert response.status_code==200,response.text
        mid=response.json()['id']
        assert client.get('/api/observations/media/'+mid+'/preview').headers['content-type']=='image/jpeg'
        assert client.get('/api/observations/media/'+mid+'/file').content==png()
        logs=client.get('/api/logs').json()
        assert any(l['eventType']=='OBSERVATION_IMPORTED' and l['details']['media_id']==mid for l in logs)
        assert client.get('/api/observations/status').json()['game_materials']==0
        assert client.post('/api/observations/media/'+mid+'/analyze',json={'regions':[{'kind':'OCR_TEXT','region':[-1,1,2,3]}]},headers=headers).status_code==422


def test_rollback_preserves_old_tables(tmp_path):
    repo,store,service=setup(tmp_path)
    store.db.execute('CREATE TABLE runs(id TEXT)');store.db.execute("INSERT INTO runs VALUES('old-run')");store.db.commit()
    store.db.executescript(Path('backend/migrations/003_observation_down.sql').read_text(encoding='utf-8'))
    assert store.rows('SELECT * FROM runs')==[{'id':'old-run'}]
    assert repo.rows('SELECT version FROM schema_migration')==[{'version':2}]
