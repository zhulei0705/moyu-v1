"""九星研究场景：历史名称作标签，执行数值全部来自显式模拟参数。"""
import copy
import json
import numpy as np
import cv2
from .world import World,TILE,COLORS

SCENE_ID='NineStarResearchDungeon'
FAULTS=('target_disappeared','path_blocked','character_dead','vision_failed','vision_persistent','stage_timeout','loot_failed','loot_persistent','bag_full','scene_load_failed','worker_interrupted','advance_persistent','path_persistent')
BOSS_COLOR=(210,90,240)
PARAMETERS={'width':22,'height':16,'entry':[2,2],'exit':[19,13],
 'walls':[[7,y] for y in range(2,13) if y not in (6,10)]+[[13,y] for y in range(3,14) if y not in (5,11)],
 'stage_positions':[[5,4],[10,5],[16,5],[18,12]],'boss_hp':75,'guard_hp':50,'player_hp':120,
 'player_attack':25,'retaliation':2,'bag_capacity':12,'gold_range':[40,80],'fragment_range':[1,3],
 'materials_per_enemy':1,'deposit_consumable_cost':1,'stage_timeout_seconds':60,'max_retries':3,'max_actions':1000}

class SceneFailure(RuntimeError):
    def __init__(self,code,message):super().__init__(message);self.code=code


def prepare_profile(twin):
    """只选单个完整历史快照，复用V1.2审核/版本规则保存可追溯配置。"""
    k=twin.knowledge;stages=k.entities('stages');bosses=k.entities('bosses');facts=k.facts()
    mechanism_snapshots={f['snapshot_id'] for f in facts if f['entity_type']=='mechanic' and f['verification_status']!='UNKNOWN'}
    snapshots=sorted({s['snapshot_id'] for s in stages},key=lambda sid:(sid not in mechanism_snapshots,sid))
    chosen=next((sid for sid in snapshots if sorted(s['ordinal'] for s in stages if s['snapshot_id']==sid)==[1,2,3,4]),None)
    selected=sorted((s for s in stages if s['snapshot_id']==chosen),key=lambda s:s['ordinal']) if chosen else []
    metadata=[];entries=[];refs=[];names=[];counts=[]
    for n in range(1,5):
        stage=next((s for s in selected if s['ordinal']==n),None)
        boss=next((b for b in bosses if stage and b['stage_id']==stage['id']),None)
        label=boss['name'] if boss else f'研究阶段{n}首领'
        names.append(label);counts.append(stage['boss_count'] if stage and type(stage['boss_count'])==int and 1<=stage['boss_count']<=4 else 1)
        metadata.append({'ordinal':n,'stage_name':stage['name'] if stage else label,'boss_name':label,
                         'boss_count':counts[-1],'label_class':'OFFICIAL_FACT' if boss else 'SIMULATED_ASSUMPTION'})
        for entity in [stage,boss]:
            if not entity:continue
            for f in facts:
                if f['entity_id']==entity['id'] and f['snapshot_id']==chosen and f['field'] in ('name','ordinal','boss_count') and f['verification_status']!='UNKNOWN':
                    entries.append({'data_class':'OFFICIAL_FACT','evidence_ids':[f['id']],'value':f['normalized_value'],'label':f['entity_type']+':'+f['field']});refs.append(f['id'])
    assumptions=[{'data_class':'SIMULATED_ASSUMPTION','evidence_ids':[],
      'value':{'parameters':copy.deepcopy(PARAMETERS),'stages':metadata,'boss_counts':counts},
      'assumption_note':'原创格地图、战斗、背包、阶段清场及随机掉落均仅用于自建测试；历史名称/数量作标签，未验证的数量取1，不复现官服机关或概率。',
      'execution_environment':'SIMULATOR'}]
    mechanisms=[]
    for f in facts:
        if f['snapshot_id']==chosen and f['entity_type']=='mechanic' and f['verification_status']!='UNKNOWN':
            mechanisms.append({'data_class':'OFFICIAL_FACT','evidence_ids':[f['id']],'value':f['normalized_value'],'label':'只读历史机制:'+f['field']})
            refs.append(f['id'])
    version=next((f['version_number'] for f in facts if f['snapshot_id']==chosen),None)
    config={'scene_id':SCENE_ID,'dungeon_id':'alama','game_version':version,'evidence_ids':list(dict.fromkeys(refs)),
      'map_regions':assumptions,'enemy_definitions':entries,'navigation_nodes':[], 'obstacles':[],
      'stage_triggers':[],'visible_mechanics':mechanisms,'reward_definitions':[], 'confidence':None,
      'validation_status':'EVIDENCE_LINKED' if refs else 'DRAFT','author':'本地九星研究构建器','reason':'历史名称与模拟规则分离，建立独立执行配置'}
    previous=twin.revisions(SCENE_ID)
    comparable={k:v for k,v in config.items() if k not in ('author','reason')}
    old={k:v for k,v in previous[0]['configuration'].items() if k not in ('execution_environment','map_coverage','current_game_verified')} if previous else None
    if old==comparable:revision=previous[0]
    else:revision=twin.save(dict(config,base_revision=previous[0]['revision'] if previous else 0))
    # 执行只解析固定结构的SIMULATED_ASSUMPTION；官网事实本身仍不可执行。
    stored=revision['configuration']['map_regions'][0]
    if stored['data_class']!='SIMULATED_ASSUMPTION' or stored['execution_environment']!='SIMULATOR':raise ValueError('执行参数须为模拟假设')
    return {'scene_id':SCENE_ID,'dungeon_id':'alama','revision_id':revision['id'],'revision':revision['revision'],
      'configuration':revision['configuration'],'parameters':copy.deepcopy(stored['value']['parameters']),
      'stage_metadata':copy.deepcopy(stored['value']['stages']),'boss_counts':list(stored['value']['boss_counts']),
      'source':'SIMULATOR','official_execution_enabled':False}

class NineStarResearchDungeon(World):
    def __init__(self,profile,seed=1,fault=None):
        if fault=='scene_load_failed':raise SceneFailure('SCENE_LOAD_FAILED','九星研究场景加载失败')
        self.profile=copy.deepcopy(profile);self.params=self.profile['parameters']
        self.width,self.height=self.params['width'],self.params['height']
        super().__init__(seed,fault)
        self.boss_positions=set();self.bag_used=0;self.deposits=0;self.consumable_cost=0
        self.hp=self.params['player_hp'];self.attack=self.params['player_attack']
        self.entry=tuple(self.params['entry']);self.player=self.entry;self.exit=tuple(self.params['exit'])
        self.walls={(x,y) for x in range(self.width) for y in range(self.height) if x in (0,self.width-1) or y in (0,self.height-1)}|{tuple(p) for p in self.params['walls']}
        self.stage_names=['等待进入']+[s['stage_name'] for s in self.profile['stage_metadata']]
        self.vision_fault_used=False
        self.bag_capacity=1 if fault=='bag_full' else self.params['bag_capacity']

    def spawn(self):
        x,y=self.params['stage_positions'][self.stage-1]
        candidates=[(x,y),(x,y+1),(x+1,y),(x+1,y+1)]
        self.boss_positions=set(candidates[:self.profile['boss_counts'][self.stage-1]])
        self.boss=(x,y);self.monsters={p:self.params['boss_hp'] for p in self.boss_positions}
        self.monsters[(x-1,y)]=self.params['guard_hp']

    def act(self,kind,target=None):
        if self.fault=='advance_persistent' and kind=='advance':
            self.actions+=1
            return [{'type':'advance_rejected','message':'阶段未实际推进','details':{'error_code':'ADVANCE_REJECTED'}}]
        if self.fault=='path_persistent' and kind=='move':
            self.actions+=1
            return [{'type':'blocked','message':'持续移动阻塞','details':{'error_code':'PATH_BLOCKED'}}]
        if self.fault=='worker_interrupted' and kind=='attack':raise SceneFailure('WORKER_INTERRUPTED','注入研究Worker中断，停止执行')
        if self.fault in ('loot_failed','loot_persistent') and kind=='loot' and (not self.fault_used or self.fault=='loot_persistent'):
            self.actions+=1;self.fault_used=True
            return [{'type':'loot_failed','message':'实际拾取接口拒绝，背包未改变','details':{'error_code':'LOOT_FAILED'}}]
        if self.fault=='character_dead' and kind=='attack' and not self.fault_used:
            self.fault_used=True;self.hp=1
        if self.fault in ('target_disappeared','path_blocked') and not self.fault_used and ((self.fault=='target_disappeared' and kind=='attack') or (self.fault=='path_blocked' and kind=='move')):
            self.fault_used=True;self.actions+=1
            if self.fault=='path_blocked':
                self.walls.add(target)
                return [{'type':'blocked','message':'动态障碍挡住下一格，需重新识别路线','details':{'position':target,'error_code':'PATH_BLOCKED'}}]
            hp=self.monsters.pop(target)
            dest=next(p for p in [(3,5),(4,8),(17,9)] if p not in self.walls|set(self.monsters)|set(self.loot)|{self.player})
            self.monsters[dest]=hp
            if target in self.boss_positions:self.boss_positions.remove(target);self.boss_positions.add(dest)
            return [{'type':'target_lost','message':'目标离开原位置，需视觉重选；未判定击杀','details':{'previous_position':target,'error_code':'TARGET_DISAPPEARED'}}]
        if kind=='deposit':
            self.actions+=1
            if not self.bag_used:return []
            slots=self.bag_used;self.bag_used=0;self.deposits+=1;self.consumable_cost+=self.params['deposit_consumable_cost']
            return [{'type':'deposit','message':'按模拟假设转存背包到模拟仓库，累计资源不变','details':{'slots_freed':slots,'consumable_cost':self.params['deposit_consumable_cost'],'cost_unit':'SIMULATED_SUPPLY_UNIT','source':'SIMULATOR','stage_id':self.stage}}]
        if kind=='loot' and self.player in self.loot and self.bag_used>=self.bag_capacity:
            self.actions+=1
            return [{'type':'bag_full','message':'背包空间不足，拾取未发生','details':{'error_code':'BAG_FULL'}}]
        if kind=='attack' and target in self.monsters and self.distance(target)<=1:
            self.actions+=1;self.monsters[target]-=self.attack;self.hp-=self.params['retaliation']
            events=[{'type':'attack','message':'模拟器实际计算伤害与反击','details':{'damage':self.attack,'target':target,'hp':self.hp}}]
            if self.monsters[target]<=0:
                del self.monsters[target]
                reward={'gold':self.rng.randint(*self.params['gold_range']),'shards':self.rng.randint(*self.params['fragment_range']),'materials':self.params['materials_per_enemy']}
                self.loot[target]=reward
                events.append({'type':'defeat','message':'目标实际死亡，生成模拟随机掉落','details':{'reward':reward,'role':'BOSS' if target in self.boss_positions else 'GUARD'}})
            for event in events:event['details'].update(source='SIMULATOR',stage_id=self.stage)
            self.boss_positions.discard(target) if target not in self.monsters else None
            if self.hp<=0:raise SceneFailure('CHARACTER_DEAD','实际反击导致角色死亡；不生成成功结算')
            return events
        try:events=super().act(kind,target)
        except RuntimeError as exc:
            if self.hp<=0:raise SceneFailure('CHARACTER_DEAD','角色生命耗尽；不生成成功结算') from exc
            raise
        for event in events:
            if event['type']=='enter':event['message']='实际进入九星研究模拟副本'
            if event['type']=='loot':self.bag_used+=1
            if event['type']=='defeat':self.boss_positions.discard(target)
            event['details'].update(source='SIMULATOR',stage_id=self.stage)
        return events

    def observation_frame(self):
        if self.fault in ('vision_failed','vision_persistent') and (not self.vision_fault_used or self.fault=='vision_persistent'):
            self.vision_fault_used=True
            return np.zeros((self.height*TILE,self.width*TILE,3),np.uint8)
        return self.render()

    def render(self):
        actual_hp=self.hp;self.hp=max(0,self.hp)
        try:frame=super().render()
        finally:self.hp=actual_hp
        for x,y in self.boss_positions & set(self.monsters):
            cv2.rectangle(frame,(x*TILE+12,y*TILE+4),(x*TILE+19,y*TILE+11),BOSS_COLOR,-1)
        frame[0,15]=(177,13,3)
        frame[0,22]=(178,13,3)
        for (x,y),hp in self.monsters.items():
            frame[y*TILE+1,x*TILE+1]=(max(0,min(255,hp)),0,0)
        for i,n in enumerate([self.inventory['gold'],self.inventory['shards'],self.inventory['materials'],self.bag_used,self.bag_capacity,self.consumable_cost]):
            frame[0,16+i]=(n&255,(n>>8)&255,0)
        return frame
