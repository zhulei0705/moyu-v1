import hashlib,json,uuid,time
from pathlib import Path
import cv2
from .store import now

def perception(obs):
    return {'player':list(obs.player),'walls':sorted(obs.walls),'monsters':sorted(obs.monsters),'bosses':sorted(obs.bosses),
      'loot':sorted(obs.loot),'entries':sorted(obs.entries),'exits':sorted(obs.exits),'stage_id':obs.stage,'hp':obs.hp,'entered':obs.entered,
      'complete':obs.complete,'exited':obs.exited,'width':obs.width,'height':obs.height,'inventory':obs.inventory,
      'bag_used':obs.bag_used,'bag_capacity':obs.bag_capacity,'consumable_cost':obs.consumable_cost,
      'confidence':obs.confidence,'confidence_basis':'DETERMINISTIC_SIMULATOR_VALIDATION','enemy_hp':{str(k):v for k,v in obs.enemy_hp.items()},
      'source':'SIMULATOR','basis':'IMAGE_PIXELS_ONLY'}

class ResearchRepository:
    def __init__(self,store):
        self.store=store
        with store.lock,store.db:
            store.db.executescript((Path(__file__).parent/'migrations/005_research.sql').read_text(encoding='utf-8-sig'))
            store.db.execute('INSERT OR IGNORE INTO schema_migration VALUES(?,?)',(5,now()))
            store.db.executescript((Path(__file__).parent/'migrations/006_research_settlement.sql').read_text(encoding='utf-8-sig'))
            store.db.execute('INSERT OR IGNORE INTO schema_migration VALUES(?,?)',(6,now()))
        # V1.3.2未知结果保留INTERRUPTED；仅记录已持久化资源，不续跑或生成成功奖励。
        interrupted=store.query("SELECT r.id FROM runs r JOIN research_run x ON x.run_id=r.id WHERE r.status='INTERRUPTED' AND NOT EXISTS(SELECT 1 FROM research_settlement s WHERE s.run_id=r.id AND s.stage_id=0)")
        for row in interrupted:
            self.settle(row['id'],0,False)
            store.log(row['id'],'RECOVER','ERROR','WORKER_INTERRUPTED','研究Worker中断，停止而非自动续写成功',{'error_code':'WORKER_INTERRUPTED','source':'SIMULATOR'})

    def bind(self,run_id,profile,fault=None):
        snapshot=dict(profile,test_override={'data_class':'SIMULATED_ASSUMPTION','fault':fault,'execution_environment':'SIMULATOR',
            'bag_capacity':1 if fault=='bag_full' else profile['parameters']['bag_capacity'],
            'stage_timeout_seconds':0 if fault=='stage_timeout' else profile['parameters']['stage_timeout_seconds']})
        self.store.execute('INSERT INTO research_run VALUES(?,?,?,?,?,?)',(run_id,profile['scene_id'],profile['dungeon_id'],profile['revision_id'],json.dumps(snapshot,ensure_ascii=False),'SIMULATOR'))

    def observe(self,run_id,frame,agent):
        png=cv2.imencode('.png',frame,[cv2.IMWRITE_PNG_COMPRESSION,9])[1].tobytes();oid=str(uuid.uuid4());error=None;obs=None
        # Retain the exact last sensor image for a filesystem fallback if SQLite
        # refuses the observation write. This is a single bounded frame.
        self.last_evidence_frame = png
        try:obs=agent.observe(frame);value=perception(obs)
        except ValueError as exc:error='VISION_FAILED';value={'source':'SIMULATOR','basis':'IMAGE_PIXELS_ONLY','error':str(exc)}
        self.store.execute('INSERT INTO agent_observation VALUES(?,?,?,?,?,?,?,?,?,?)',
          (oid,run_id,obs.stage if obs else 0,now(),'simulator-color-hud-v1.3',hashlib.sha256(png).hexdigest(),png,json.dumps(value,ensure_ascii=False),error,'SIMULATOR'))
        self.store.evidence.frame(oid,png)
        self.store.execute('UPDATE agent_observation SET frame_png=? WHERE id=?',(b'',oid))
        return oid,obs,value

    def decision(self,run_id,before_id,before,decision,after_id,after,events,latency,error=None,timeline=None):
        did=str(uuid.uuid4());state=perception(before) if before else {'source':'SIMULATOR','basis':'IMAGE_PIXELS_ONLY','status':'UNKNOWN'}
        details={'id':did,'run_id':run_id,'stage_id':before.stage if before else 0,'observation_id':before_id,
          'after_observation_id':after_id,'world_state':state,'selected_goal':getattr(decision,'goal',{'kind':'RECOVER_VISION'}),
          'action_type':decision.kind if decision else 'observe','decision_reason':getattr(decision,'reason','图像未识别出唯一角色，有限重新采样，未执行世界动作'),
          'expected_result':{'enter':'已进入阶段1','move':'角色到达所选相邻格','attack':'实际攻击并重新观察目标/生命','loot':'掉落减少且背包资源增加','advance':'实际清场后阶段前进或出口开放','exit':'角色实际退出','deposit':'背包空位释放、累计模拟资源守恒'}.get(decision.kind if decision else 'observe','获得可识别画面'),
          'actual_result':{'perception':perception(after) if after else None,'events':events,'verified':error is None,'timeline':timeline},
          'decision_latency':latency,'error_code':error,'route':getattr(decision,'route',[]),'created_at':now(),'source':'SIMULATOR'}
        self.store.execute('INSERT INTO agent_decision VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
          (did,run_id,details['stage_id'],before_id,after_id,json.dumps(state,ensure_ascii=False),json.dumps(details['selected_goal'],ensure_ascii=False),details['action_type'],details['decision_reason'],details['expected_result'],json.dumps(details['actual_result'],ensure_ascii=False),latency,error,json.dumps(details['route']),details['created_at'],'SIMULATOR'))
        self.store.log(run_id,decision.state if decision else 'RECOVER','WARN' if error else 'INFO','AGENT_DECISION',details['decision_reason'],details)
        return details

    def decisions(self,run_id=None,limit=200):
        rows=self.store.query('SELECT * FROM agent_decision'+(' WHERE run_id=?' if run_id else '')+' ORDER BY rowid DESC LIMIT ?',(run_id,limit) if run_id else (limit,))
        for row in rows:
            for field in ('world_state','selected_goal','actual_result','route'):row[field]=json.loads(row[field])
        return rows

    def settle(self,run_id,duration,success,world=None,actions=0):
        timeline={'settlement_started':time.monotonic(),'run_id':run_id}
        from .research_scene import SceneFailure
        context=self.store.query('SELECT * FROM research_run WHERE run_id=?',(run_id,))
        if not context:return
        if duration==0:
            from datetime import datetime,timezone
            rows=self.store.query('SELECT started,ended FROM runs WHERE id=?',(run_id,))
            if rows:
                end=datetime.fromisoformat(rows[0]['ended']) if rows[0]['ended'] else datetime.now(timezone.utc)
                duration=max(0,(end-datetime.fromisoformat(rows[0]['started'])).total_seconds())
        logs=self.store.query('SELECT * FROM logs WHERE runId=? ORDER BY id',(run_id,))
        timeline['events_loaded']=time.monotonic()
        stages={i:{'gold':0,'shards':0,'materials':0,'cost':0} for i in range(1,5)}
        from datetime import datetime,timezone
        stage_starts={};cleared=set();run_end=datetime.now(timezone.utc)
        for e in logs:
            if e['eventType'] in ('enter','stage'):
                stage=e['details'].get('stage_id')
                if stage in stages:
                    stage_starts[stage]=datetime.fromisoformat(e['timestamp'])
                    if stage>1:cleared.add(stage-1)
            if e['eventType']=='complete':cleared.add(4)
            if e['eventType']=='exit':run_end=datetime.fromisoformat(e['timestamp'])
            if e['eventType'] in ('loot','deposit'):
                stage=e['details'].get('stage_id')
                if stage not in stages:raise SceneFailure('SETTLEMENT_MISMATCH','资源事件缺少有效阶段')
                if e['eventType']=='loot':
                    for k in ('gold','shards','materials'):stages[stage][k]+=e['details']['reward'][k]
                else:stages[stage]['cost']+=e['details']['consumable_cost']
        total={k:sum(s[k] for s in stages.values()) for k in ('gold','shards','materials','cost')}
        if world is not None and ({k:total[k] for k in ('gold','shards','materials')}!=world.inventory or total['cost']!=world.consumable_cost):
            raise SceneFailure('SETTLEMENT_MISMATCH','持久资源事件与实际背包/成本不一致，需要人工审核')
        if success:
            if cleared!={1,2,3,4}:raise SceneFailure('SETTLEMENT_MISMATCH','缺少全部四阶段完成事件')
            if world is None or not world.exited or not world.complete or world.stage!=4:raise SceneFailure('SETTLEMENT_MISMATCH','未实际退出四阶段副本')
            if {k:total[k] for k in ('gold','shards','materials')}!=world.inventory or total['cost']!=world.consumable_cost:raise SceneFailure('SETTLEMENT_MISMATCH','资源事件与环境背包/成本不一致')
        timestamp=now()
        timeline['validation_finished']=time.monotonic()
        with self.store.lock,self.store.db:
            timeline['transaction_lock_acquired']=time.monotonic()
            if self.store.db.execute('SELECT 1 FROM research_settlement WHERE run_id=? AND stage_id=0',(run_id,)).fetchone():return
            status=self.store.db.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()
            if success and (not status or status[0]!='RUNNING'):
                raise SceneFailure('SETTLEMENT_INVALID_STATE','仅运行中的已验证任务允许成功结算')
            for stage,values in [(0,total),*stages.items()]:
                stage_duration=duration if stage==0 else (max(0,(stage_starts.get(stage+1,run_end)-stage_starts[stage]).total_seconds()) if stage in stage_starts else 0)
                stage_success=bool(success) if stage==0 else stage in cleared
                self.store.db.execute('INSERT INTO research_settlement VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                  (run_id,'alama',stage,values['gold'],values['shards'],values['materials'],values['cost'],stage_duration,int(stage_success),timestamp,'SIMULATOR','SIMULATED_SUPPLY_UNIT'))
            if success:
                self.store.db.execute("UPDATE runs SET status='COMPLETED',ended=?,duration=?,rewards=? WHERE id=?",(timestamp,duration,json.dumps(world.inventory),run_id))
            sequence=self.store.db.execute('SELECT COUNT(*) FROM logs WHERE runId=?',(run_id,)).fetchone()[0]+1
            self.store.db.execute('INSERT INTO logs(timestamp,sequence,runId,stage,level,eventType,message,details) VALUES(?,?,?,?,?,?,?,?)',
              (timestamp,sequence,run_id,'SETTLEMENT','INFO','RESEARCH_SETTLEMENT','按实际模拟事件保存独立结算；不换算人民币',json.dumps({'run_id':run_id,'source':'SIMULATOR','success':success,'resource_basis':'VERIFIED_SIMULATOR_INVENTORY' if world is not None else 'PERSISTED_EVENTS_ONLY','rewards':{k:total[k] for k in ('gold','shards','materials')},'consumable_cost':total['cost'],'cost_unit':'SIMULATED_SUPPLY_UNIT','actions':actions,'timeline':timeline},ensure_ascii=False)))

    def earnings(self):
        rows=self.store.query('SELECT s.*,r.status FROM research_settlement s JOIN runs r ON r.id=s.run_id WHERE s.stage_id=0 ORDER BY s.settlement_time DESC')
        finished=[r for r in rows if r['status'] in ('COMPLETED','FAILED')]
        complete=[r for r in rows if r['success']]
        return {'source':'SIMULATOR','currency_conversion':None,'cost_unit':'SIMULATED_SUPPLY_UNIT','completed':len(complete),'interrupted':sum(r['status']=='INTERRUPTED' for r in rows),
          'failed':sum(r['status']=='FAILED' for r in rows),'stopped':sum(r['status']=='STOPPED' for r in rows),
          'success_rate':len(complete)/len(finished) if finished else None,'sample_count':len(finished),
          'simulated_gold':sum(r['simulated_gold'] for r in complete),'simulated_fragments':sum(r['simulated_fragments'] for r in complete),
          'simulated_materials':sum(r['simulated_materials'] for r in complete),'consumable_cost':sum(r['consumable_cost'] for r in complete),'items':rows}
