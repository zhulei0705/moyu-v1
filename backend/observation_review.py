import json
import math
from .observation_store import uid
from .observation_vision import validate_region
from .store import now

FACT_FIELDS={'visible_text','entry_coordinates','map_name','player_position','monster_name','boss_name','quest_text','item_name','visible_hp','minimum_level','minimum_battle_power','game_build_number'}
ANNOTATION_KINDS={'SCENE_REGION','PLAYER','MONSTER','BOSS','OBSTACLE','WALKABLE_REGION','ENTRY','EXIT','MECHANIC','TEXT_REGION','ITEM'}
COORDINATES={'SCREEN_PIXEL','MAP_PIXEL','GAME_COORDINATE'}


class ReviewService:
    def __init__(self,store,knowledge): self.store,self.knowledge=store,knowledge

    def review(self,observation_id,body):
        rows=self.store.rows('SELECT * FROM observation WHERE id=?',(observation_id,))
        if not rows: raise ValueError('观察结果不存在')
        obs=rows[0];media=self.store.media(obs['media_id'])
        if obs['status'] in ('CONFIRMED','REJECTED'): raise ValueError('已审核结果不可重复审核；应重新分析或建立新标注')
        action=body['action'];field=body.get('field','visible_text')
        if action not in ('confirm','correct','reject'): raise ValueError('未知审核动作')
        if len(body.get('reviewer','').strip())<2 or len(body.get('rationale','').strip())<3: raise ValueError('必须填写审核者与依据')
        value=body.get('corrected_value') if action=='correct' else obs['value']
        if action!='reject':
            if field not in FACT_FIELDS: raise ValueError('只允许可见值事实，不允许推断概率、掉落规则或技能机制')
            if value is None or value=='': raise ValueError('UNKNOWN 结果须先人工纠正，不能直接确认')
            if field=='entry_coordinates' and (not isinstance(value,list) or len(value)!=2 or any(type(v)!=int for v in value)):
                raise ValueError('游戏坐标必须为两个整数，不能混用屏幕像素')
            if field=='entry_coordinates' and obs['kind']!='GAME_COORDINATE': raise ValueError('须审核游戏坐标文字结果，不可从角色屏幕区域推断游戏坐标')
            if field in ('minimum_level','minimum_battle_power','visible_hp') and (type(value)!=int or value<0): raise ValueError('可见数值必须为非负整数')
        review_id=uid();fact_id=None
        with self.store.lock,self.store.db:
            self.store.db.execute('INSERT INTO observation_review VALUES(?,?,?,?,?,?,?)',
                (review_id,observation_id,action,body['reviewer'].strip(),body['rationale'].strip(),json.dumps(value,ensure_ascii=False) if action=='correct' else None,now()))
            self.store.db.execute('UPDATE observation SET status=? WHERE id=?',('REJECTED' if action=='reject' else 'CONFIRMED',observation_id))
            if action!='reject':
                fact_id=uid();data_class='OBSERVED_FACT' if media['source_type']=='GAME_OBSERVATION' else 'SIMULATED_ASSUMPTION'
                self.store.db.execute('INSERT INTO reviewed_observation_fact VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (fact_id,observation_id,review_id,media['id'],field,json.dumps(value,ensure_ascii=False),media['game_version'],media['source_type'],
                     data_class,'VISIBLE_IN_FRAME_ONLY','REVIEWED',now()))
        conflicts=[]
        if fact_id and media['source_type']=='GAME_OBSERVATION':
            for official in self.knowledge.facts('dungeon'):
                if official['field']==field and official['normalized_value'] is not None and official['normalized_value']!=value:
                    conflict_id=uid()
                    self.store.execute('INSERT INTO observation_conflict VALUES(?,?,?,?,?,?,?)',
                        (conflict_id,fact_id,official['id'],field,'OPEN','画面观察与历史官网值不同，仅证明该画面可见值，不自动决定版本有效性。',now()))
                    conflicts.append(conflict_id)
                    self.store.event(observation_id,'FACT_CONFLICT','保留观察与官网差异',{'conflict_id':conflict_id,'official_fact_id':official['id'],'observed_fact_id':fact_id})
        self.store.event(observation_id,'OBSERVATION_REVIEWED','人工审核观察结果',{'review_id':review_id,'action':action,'reviewer':body['reviewer']})
        if fact_id:self.store.event(observation_id,'FACT_CONFIRMED','形成仅对当前画面可见值有效的已审核事实',{'fact_id':fact_id,'scope':'VISIBLE_IN_FRAME_ONLY','source_type':media['source_type']})
        return {'review_id':review_id,'fact_id':fact_id,'conflict_ids':conflicts}

    def annotate(self,media_id,body):
        media=self.store.media(media_id)
        annotations=body['annotations']
        if media['file_type'] not in ('PNG','JPG'):raise ValueError('请先抽帧，再标注图像')
        if len(annotations)>200: raise ValueError('每版本最多200个标注')
        if len(body.get('author','').strip())<2 or len(body.get('reason','').strip())<3: raise ValueError('必须填写标注者及理由')
        for a in annotations:
            if not isinstance(a,dict):raise ValueError('标注必须为对象')
            if a.get('kind') not in ANNOTATION_KINDS or a.get('coordinate_system') not in COORDINATES: raise ValueError('标注类别或坐标体系无效')
            if a['coordinate_system']=='SCREEN_PIXEL': a['region']=validate_region(a['region'],media['width'],media['height'])
            else:
                region=a.get('region')
                if not isinstance(region,list) or len(region)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in region) or region[2]<=0 or region[3]<=0 or not isinstance(a.get('coordinate_basis'),str) or not a['coordinate_basis'].strip():raise ValueError('地图/游戏坐标必须有有限区域及显式坐标依据，不自动从截图转换')
            a['coverage']='VISIBLE_PORTION_ONLY'
        with self.store.lock,self.store.db:
            last=self.store.db.execute('SELECT id,revision FROM annotation_revision WHERE media_id=? ORDER BY revision DESC LIMIT 1',(media_id,)).fetchone()
            current=last['revision'] if last else 0
            if body.get('base_revision',0)!=current: raise ValueError('标注版本冲突，请加载最新版本后再保存')
            revision_id=uid()
            self.store.db.execute('INSERT INTO annotation_revision VALUES(?,?,?,?,?,?,?,?)',
                (revision_id,media_id,current+1,last['id'] if last else None,json.dumps(annotations,ensure_ascii=False),body['author'],body['reason'],now()))
        self.store.event(media_id,'ANNOTATION_CREATED','保存人工标注版本',{'revision_id':revision_id,'revision':current+1,'count':len(annotations),'coverage':'VISIBLE_PORTION_ONLY'})
        return self.store.rows('SELECT * FROM annotation_revision WHERE id=?',(revision_id,))[0]
