"""独立研究配置。引用证据的可见值，不驱动原 Agent，也不推断机制。"""
import json
import math
from pathlib import Path
from typing import Any, Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from .observation_store import uid
from .store import now

SECTIONS=('map_regions','navigation_nodes','obstacles','enemy_definitions','stage_triggers','visible_mechanics','reward_definitions')

class TwinBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scene_id:str=Field(min_length=1,max_length=100,pattern=r'^[a-zA-Z0-9_-]+$')
    dungeon_id:Literal['alama']='alama'
    game_version:str|None=Field(default=None,max_length=200)
    evidence_ids:list[str]=Field(default_factory=list,max_length=500)
    map_regions:list[dict[str,Any]]=Field(default_factory=list,max_length=200)
    navigation_nodes:list[dict[str,Any]]=Field(default_factory=list,max_length=200)
    obstacles:list[dict[str,Any]]=Field(default_factory=list,max_length=200)
    enemy_definitions:list[dict[str,Any]]=Field(default_factory=list,max_length=200)
    stage_triggers:list[dict[str,Any]]=Field(default_factory=list,max_length=200)
    visible_mechanics:list[dict[str,Any]]=Field(default_factory=list,max_length=200)
    reward_definitions:list[dict[str,Any]]=Field(default_factory=list,max_length=200)
    confidence:float|None=Field(default=None,ge=0,le=1)
    validation_status:Literal['DRAFT','EVIDENCE_LINKED']='DRAFT'
    base_revision:int=Field(default=0,ge=0)
    author:str=Field(min_length=2,max_length=100)
    reason:str=Field(min_length=3,max_length=2000)

class TwinService:
    def __init__(self,store,knowledge):
        self.store,self.knowledge=store,knowledge
        with store.lock:
            store.db.executescript((Path(__file__).parent/'migrations/004_twin.sql').read_text(encoding='utf-8-sig'))
            store.db.execute('INSERT OR IGNORE INTO schema_migration VALUES(?,?)',(4,now()))
            store.db.commit()

    def evidence(self):
        items=[]
        for f in self.knowledge.facts():
            if f['verification_status']=='UNKNOWN':continue
            items.append({'id':f['id'],'data_class':'OFFICIAL_FACT','field':f['field'],'value':f['normalized_value'],
                          'game_version':f['version_number'],'version_label':f['version_label'],'source_url':f['source_url'],
                          'scope':'HISTORICAL_OFFICIAL_PAGE','current_game_verified':False})
        for f in self.store.rows('SELECT * FROM reviewed_observation_fact'):
            items.append(dict(f,game_version=f['game_version']))
        for r in self.store.rows('SELECT * FROM annotation_revision'):
            m=self.store.media(r['media_id'])
            items.append({'id':r['id'],'data_class':'OBSERVED_FACT' if m['source_type']=='GAME_OBSERVATION' else 'SIMULATED_ASSUMPTION',
                          'value':r['annotations'],'field':'annotations','game_version':m['game_version'],'media_id':m['id'],
                          'scope':'VISIBLE_PORTION_ONLY'})
        return items

    def revisions(self,scene_id=None):
        rows=self.store.rows('SELECT * FROM twin_scene_revision'+(' WHERE scene_id=?' if scene_id else '')+' ORDER BY created_at DESC',
                             (scene_id,) if scene_id else ())
        return [dict(r,configuration=json.loads(r['configuration'])) for r in rows]

    def save(self,body):
        data=TwinBody.model_validate(body).model_dump()
        evidence={r['id']:r for r in self.evidence()}
        ids=data['evidence_ids']
        if len(ids)!=len(set(ids)) or any(i not in evidence for i in ids):raise ValueError('证据须唯一并引用现存官网事实、已审核事实或人工标注版本')
        linked=[]
        for section in SECTIONS:
            for entry in data[section]:
                cls=entry.get('data_class')
                if cls not in ('OFFICIAL_FACT','OBSERVED_FACT','SIMULATED_ASSUMPTION'):raise ValueError('每项必须明确数据类别')
                refs=entry.get('evidence_ids',[])
                if not isinstance(refs,list) or any(i not in ids for i in refs):raise ValueError('条目证据必须包含在场景 evidence_ids 内')
                if cls=='SIMULATED_ASSUMPTION':
                    if not entry.get('assumption_note') or entry.get('execution_environment')!='SIMULATOR':
                        raise ValueError('模拟假设须说明测试依据并限定 SIMULATOR')
                    if any(evidence[i]['data_class']!='SIMULATED_ASSUMPTION' for i in refs):raise ValueError('模拟假设不得冒用真实事实证据')
                else:
                    if not refs or any(evidence[i]['data_class']!=cls for i in refs):raise ValueError('真实条目必须引用同类证据')
                    # 条目只保存证据的原值。任何扩展推断必须另建模拟假设。
                    if set(entry)-{'data_class','evidence_ids','value','label'}:raise ValueError('证据条目只允许类别、引用、标签和原值；机制推断请另建模拟假设')
                    if len(refs)!=1 or entry.get('value')!=evidence[refs[0]]['value']:raise ValueError('事实值必须与所引用证据一致')
                    if data['game_version']!=evidence[refs[0]]['game_version']:raise ValueError('场景版本必须与证据已知版本一致，未知保持 NULL')
                linked.extend(refs)
        if data['validation_status']=='EVIDENCE_LINKED' and (not linked or set(ids)!=set(linked)):
            raise ValueError('EVIDENCE_LINKED 须有条目且全部证据实际被引用；不代表地图完整或当前官服已验证')
        if data['confidence'] is not None and not math.isfinite(data['confidence']):raise ValueError('置信度必须有限')
        scene=data['scene_id'];base=data.pop('base_revision');author=data.pop('author');reason=data.pop('reason')
        data.update(execution_environment='RESEARCH_ONLY',map_coverage='VISIBLE_PORTION_ONLY',current_game_verified=False)
        with self.store.lock,self.store.db:
            last=self.store.db.execute('SELECT id,revision FROM twin_scene_revision WHERE scene_id=? ORDER BY revision DESC LIMIT 1',(scene,)).fetchone()
            revision=last['revision'] if last else 0
            if revision!=base:raise ValueError('场景版本冲突，请加载最新版本')
            rid=uid()
            self.store.db.execute('INSERT INTO twin_scene_revision VALUES(?,?,?,?,?,?,?,?)',
                (rid,scene,revision+1,last['id'] if last else None,json.dumps(data,ensure_ascii=False,allow_nan=False),author,reason,now()))
        self.store.event(scene,'TWIN_SCENE_UPDATED','保存独立九星研究场景版本',{'scene_id':scene,'revision':revision+1,'evidence_ids':ids})
        return self.revisions(scene)[0]

def router(service):
    routes=APIRouter(prefix='/api/observations/twins',tags=['九星研究场景'])
    @routes.get('')
    def listing():return {'items':service.revisions()}
    @routes.get('/evidence')
    def evidence():return {'items':service.evidence()}
    @routes.get('/{scene_id}')
    def detail(scene_id:str):
        rows=service.revisions(scene_id)
        if not rows:raise HTTPException(404,'场景不存在')
        return {'items':rows}
    @routes.post('')
    def save(body:TwinBody):
        try:return service.save(body.model_dump())
        except ValueError as exc:raise HTTPException(422,str(exc))
    return routes
