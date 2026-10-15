import threading
import time
import uuid
import json
import logging
import cv2
import sqlite3
from .world import World, STAGE_NAMES
from .agent import Agent
from .store import now
from .research_scene import NineStarResearchDungeon,SCENE_ID,SceneFailure
from .research_agent import ResearchAgent
from .research_repository import ResearchRepository
from .research_controller import ResearchController
from .stability import Stability


class Runtime:
    @property
    def stop_requested(self):return self._stop_event.is_set()
    @stop_requested.setter
    def stop_requested(self,value):
        self._stop_event.set() if value else self._stop_event.clear()
    @property
    def state(self):return self._state
    @state.setter
    def state(self,value):
        if getattr(self,'_state',None)!=value:
            previous=getattr(self,'_state',None)
            active_clock=time.monotonic()-getattr(self,'pause_total',0)
            budget=Stability.budgets.get(previous)
            if getattr(self,'profile',None) and budget and active_clock-getattr(self,'state_changed',active_clock)>budget:
                raise SceneFailure('STATE_TIMEOUT','状态活动时间超限：'+previous)
            self._state=value
            self.state_changed=active_clock
            self.store.record_state(getattr(self,'run_id',None),value,{'paused':getattr(self,'paused',False),'source':'SIMULATOR'})
    def __init__(self, store, tick=0.08, action_timeout=2, run_timeout=180):
        self.store, self.tick = store, tick
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.wake = threading.Event()
        self.thread = None
        self._stop_event = threading.Event()
        self.control_session = (None,self._stop_event)
        self.stop_requested = False
        self.paused = False
        self.state = 'IDLE'
        self.run_id = None
        self.world = None
        self.started = None
        self.error = None
        self.stop_reason = None
        self.rounds = 0
        self.completed_session = 0
        self.finished_elapsed = 0
        self.action_timeout, self.run_timeout = action_timeout, run_timeout
        self.pause_began = None
        self.pause_total = 0
        self.research = ResearchRepository(store)
        self.research_controller = ResearchController(self.research)
        self.scene_id = "original"
        self.profile = None
        self.profile_factory = None
        self.last_decision = None
        self.stability = Stability(store)

    def log(self, event, message, details=None, level='INFO'):
        self.store.log(self.run_id, self.state, level, event, message, details)

    def start(self, rounds=1, seed=1, fault=None, scene_id="original"):
        with self.lock:
            if not isinstance(rounds,int) or isinstance(rounds,bool) or not 1<=rounds<=100:
                raise ValueError('连续轮次必须为1至100')
            if self.thread and self.thread.is_alive(): raise ValueError('Agent 已在执行，禁止重复启动')
            if self.stability.review_required:raise ValueError('异常持久化未确认，需人工检查并重启服务')
            self.stability.storage_guard()
            if scene_id not in ('original',SCENE_ID):raise ValueError('未知执行场景')
            if scene_id==SCENE_ID and self.profile_factory is None:raise ValueError('九星研究配置服务未连接')
            self.scene_id=scene_id
            self.profile=self.profile_factory() if scene_id==SCENE_ID else None
            self.last_decision=None
            self.stability.reset()
            self._stop_event=threading.Event()
            self.stop_requested, self.paused, self.error = False, False, None
            self.stop_reason = None
            self.rounds, self.completed_session = rounds, 0
            self.finished_elapsed, self.pause_total, self.pause_began = 0, 0, None
            self.started = time.monotonic()
            self.run_id = str(uuid.uuid4())
            self.state = 'PREPARE'
            self.thread = threading.Thread(target=self.worker, args=(rounds, seed, fault), daemon=True)
            self.control_session=(self.thread,self._stop_event)
            self.thread.start()

    def control(self, action):
        if action not in ('pause','resume','stop','emergency-stop'):raise ValueError('未知控制动作')
        target_thread=None
        # Latch stop before waiting for an in-flight action/SQLite write barrier.
        if action in ('stop','emergency-stop'):
            target_thread,stop_event=self.control_session
            if not target_thread or not target_thread.is_alive():raise ValueError('当前没有执行中的 Agent')
            stop_event.set()
            self.wake.set()
        with self.lock:
            if target_thread is not None and self.thread is not target_thread:raise ValueError('任务已变更，停止请求未应用到新任务')
            if target_thread is not None and not target_thread.is_alive():return
            if target_thread is None and (not self.thread or not self.thread.is_alive()):raise ValueError('当前没有执行中的 Agent')
            if action == 'pause':
                if self.paused: raise ValueError('已暂停')
                self.paused = True
                self.pause_began = time.monotonic()
                self.log('pause','用户暂停；动作屏障已生效')
            elif action == 'resume':
                if not self.paused: raise ValueError('当前未暂停')
                self.paused = False
                self.pause_total += time.monotonic()-self.pause_began
                self.pause_began = None
                self.log('resume','用户恢复运行')
            else:
                self.stop_requested = True
                self.stop_reason = 'EMERGENCY_STOP' if action=='emergency-stop' else 'USER_STOP'
                self.paused = False
                self.log(action,'紧急停止执行' if action=='emergency-stop' else '用户停止执行', level='WARN')
            self.wake.set()
            self.store.record_state(self.run_id,'PAUSED' if self.paused else ('STOP_REQUESTED' if self.stop_requested else self.state),{'control_action':action,'source':'SIMULATOR'})
            self.condition.notify_all()
        if action in ('stop','emergency-stop'):
            target_thread.join(timeout=3)
            if target_thread.is_alive(): raise RuntimeError('Worker 未在期限内释放')

    def worker(self, rounds, seed, fault):
        active = False
        try:
            for index in range(rounds):
                with self.lock:
                    while self.paused and not self.stop_requested:
                        self.condition.wait(self.tick)
                    if self.stop_requested: break
                    if index: self.run_id = str(uuid.uuid4())
                    self.world = None
                    self.stability.storage_guard()
                    self.stability.last_progress=time.monotonic()
                    self.stability.state=None
                    agent = ResearchAgent(self.profile['stage_metadata']) if self.profile else Agent()
                    self.state = 'PREPARE'
                    began = time.monotonic()
                    self.pause_total = 0
                    self.state_changed=began
                    self.store.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)',
                                       (self.run_id,'RUNNING',now(),None,None,seed+index,None,None))
                    active = True
                    if self.profile:self.research.bind(self.run_id,self.profile,fault if index==0 else None)
                    self.world = NineStarResearchDungeon(self.profile,seed+index,fault if index==0 else None) if self.profile else World(seed+index,fault if index==0 else None)
                    stage_active_began=time.monotonic();stage_pause_offset=self.pause_total;last_stage=0
                    if self.profile:
                        self.state='QUALIFY'
                        self.log('CHECK_ELIGIBILITY','仅在自建模拟器检查角色与场景资格',{'source':'SIMULATOR','scene_id':self.scene_id,'twin_revision_id':self.profile['revision_id']})
                    self.log('prepare','创建实际模拟世界与独立视觉 Agent', {'source':'SIMULATED','seed':seed+index})
                steps = 0
                while True:
                    with self.lock:
                        self.stability.pulse()
                        if self.stop_requested: break
                        if not self.paused:
                            self.stability.storage_guard()
                            if time.monotonic()-began-self.pause_total > self.run_timeout: raise TimeoutError('副本运行超时')
                            self.state = 'SEARCH'
                            if self.profile:
                                stage_budget=0 if self.world.fault=='stage_timeout' else self.profile['parameters']['stage_timeout_seconds']
                                if self.world.entered and time.monotonic()-stage_active_began-(self.pause_total-stage_pause_offset)>stage_budget:
                                    raise SceneFailure('STAGE_TIMEOUT','BOSS阶段活动时间超限，停止执行')
                                after=self.research_controller.step(self,agent)
                                if after and after.stage!=last_stage:
                                    stage_active_began=time.monotonic();stage_pause_offset=self.pause_total;last_stage=after.stage
                            else:
                                before = agent.observe(self.world.render())
                                self.log('perception','OpenCV 图像识别完成', {'stage':before.stage,'monsters':len(before.monsters),'loot':len(before.loot),'player':before.player})
                                decision = agent.decide(before)
                                self.state = decision.state
                                self.log('decision','选择动作 '+decision.kind, {'target':decision.target})
                                action_began = time.monotonic()
                                events = agent.act(self.world.act, decision)
                                for e in events: self.log(e['type'],e['message'],e['details'])
                                if time.monotonic()-action_began > self.action_timeout: raise TimeoutError('动作超时')
                                after = agent.observe(self.world.render())
                                if not agent.verify(before,after,decision,events):
                                    self.state = 'RECOVER'
                                    self.log('recover','验证未通过，重新感知并有限重试', {'retry':agent.retries},'WARN')
                                else: self.log('verify','动作结果验证通过', {'action':decision.kind})
                            steps += 1
                            if after is not None and after.exited:
                                self.state = 'SETTLEMENT'
                                rewards = self.world.inventory.copy()
                                self.research.settle(self.run_id,time.monotonic()-began,True,self.world,steps) if self.profile else self.store.settle(self.run_id,time.monotonic()-began,rewards,steps)
                                self.log('round_committed','本轮真实退出与结算事务已提交',{'run_id':self.run_id,'round_index':index,'monotonic':time.monotonic(),'actions':steps})
                                self.completed_session += 1
                                active = False
                                break
                            if steps > 1000: raise RuntimeError('动作数超过上限')
                    self.wake.wait(self.tick)
                    self.wake.clear()
                if self.stop_requested: break
        except Exception as exc:
            self.stop_reason=getattr(exc,'code','DATABASE_WRITE_ERROR' if isinstance(exc,sqlite3.Error) else 'WORKER_ERROR')
            if isinstance(exc,sqlite3.Error):
                self.store.evidence.write_error=str(exc)
                self.store.evidence.last_stop='DATABASE_WRITE_ERROR'
                self.store.evidence.persist_monitor()
                png=getattr(self.research,'last_evidence_frame',None)
                if png:
                    try:self.store.evidence.capture_debug(png,anomaly=True)
                    except OSError:logging.exception('故障画面备用存储失败，保留已持久化观察并停止')
            with self.lock:
                self._state='FAILED'
                self.error = str(exc)
                def attempt(callback):
                    try:callback()
                    except Exception as cleanup:
                        self.stability.review_required=True
                        self.stability.cleanup_errors.append(str(cleanup))
                        logging.exception('Worker失败清理无法持久化，禁止再次启动')
                attempt(lambda:self.store.record_state(self.run_id,'FAILED',{'error':str(exc),'reason':self.stop_reason}))
                if getattr(self,'on_failure',None):attempt(lambda:self.on_failure(exc))
                attempt(lambda:self.log('error','Worker 执行失败', {'error':str(exc),'error_code':getattr(exc,'code','WORKER_ERROR'),'source':'SIMULATOR' if self.profile else 'SIMULATED'},'ERROR'))
                if active:
                    attempt(lambda:self.store.execute("UPDATE runs SET status='FAILED', ended=?, duration=?, error=? WHERE id=? AND status='RUNNING'", (now(),time.monotonic()-began,str(exc),self.run_id)))
                if self.profile and active:attempt(lambda:self.research.settle(self.run_id,time.monotonic()-began,False,self.world))
                if self.profile and active:attempt(lambda:self.stability.checkpoint(self,'FAILED'))
                active = False
        finally:
            with self.lock:
                try:
                    if active:
                        self.store.execute("UPDATE runs SET status='STOPPED', ended=?, duration=? WHERE id=? AND status='RUNNING'",(now(),time.monotonic()-began,self.run_id))
                    if active and self.profile:
                        self.research.settle(self.run_id,time.monotonic()-began,False,self.world)
                        self.stability.checkpoint(self,'STOPPED')
                    self.store.record_state(self.run_id,'STOPPED',{'source':'SIMULATOR','reason':self.stop_reason})
                    self._state='STOPPED'
                    self.log('stopped','Worker 已释放执行资源',{'run_id':self.run_id,'reason':self.stop_reason})
                except Exception as cleanup:
                    self.stability.review_required=True
                    self.stability.cleanup_errors.append(str(cleanup))
                    logging.exception('Worker终止持久化失败，需要人工审核')
                finally:
                    self._state='STOPPED'
                    self.finished_elapsed = time.monotonic()-self.started
                    self.paused = False
                    self.pause_began=None
                    self.stability.pulse()

    def status(self):
        acquired=self.lock.acquire(timeout=.05)
        try:
            return {'state':self.state,'paused':self.paused,'running':bool(self.thread and self.thread.is_alive()),
                    'storage':self.store.evidence.status(),'runId':self.run_id,'elapsedSeconds':time.monotonic()-self.started if self.started and self.thread and self.thread.is_alive() else self.finished_elapsed,
                    'completedSession':self.completed_session,'rounds':self.rounds,'error':self.error,'stop_reason':self.stop_reason,'source':'SIMULATED','scene_id':self.scene_id,'twin_revision_id':self.profile['revision_id'] if self.profile else None,'last_decision':self.last_decision,'stability':self.stability.status(self),'action_barrier_busy':not acquired,'stop_requested':self.stop_requested}
        finally:
            if acquired:self.lock.release()

    def dungeon(self):
        with self.lock:
            if not self.world: return {'loaded':False,'stage':None,'hp':None,'attack':None,'bossRemaining':None,'actions':None,'bag_used':None,'bag_capacity':None,'consumable_cost':None,'source':'SIMULATED','scene_id':self.scene_id}
            w = self.world
            return {'loaded':True,'stage':w.stage,'hp':w.hp,'attack':w.attack,'bossRemaining':len(w.monsters),
                    'stageName':getattr(w,'stage_names',STAGE_NAMES)[w.stage], 'targets':[{'position':p,'hp':hp,'role':'BOSS' if p in getattr(w,'boss_positions',{w.boss}) else 'GUARD'} for p,hp in w.monsters.items()],
                    'complete':w.complete,'exited':w.exited,'actions':w.actions,'source':'SIMULATED','scene_id':self.scene_id,'player':w.player,'width':w.width,'height':w.height,'route':self.last_decision['route'] if self.last_decision else [],'bag_used':getattr(w,'bag_used',0),'bag_capacity':getattr(w,'bag_capacity',0),'consumable_cost':getattr(w,'consumable_cost',0),'environment_state_basis':'SIMULATOR_UI_ONLY'}

    def frame(self):
        with self.lock:
            w = self.world or World()
            return cv2.imencode('.png',w.render())[1].tobytes()
