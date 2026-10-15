import time
from .research_repository import perception
from .research_scene import SceneFailure

class ResearchController:
    def __init__(self,repository):self.repository=repository

    def step(self,runtime,agent):
        repo=self.repository;world=runtime.world
        pipeline_began=time.monotonic()
        timeline={'worker_received':pipeline_began,'run_id':runtime.run_id}
        runtime.stability.storage_guard()
        before_id,before,before_state=repo.observe(runtime.run_id,world.observation_frame(),agent)
        timeline['observation_committed']=time.monotonic()
        if before is None or before.confidence is None or before.confidence<0.9:
            agent.retries+=1
            repo.decision(runtime.run_id,before_id,None,None,None,None,[],0,'VISION_FAILED')
            runtime.state='RECOVER';runtime.log('recover','视觉失败，未执行动作，最多重新采样3次',{'retry':agent.retries,'error_code':'VISION_FAILED'},'WARN')
            runtime.stability.check(runtime,'RECOVER')
            runtime.stability.result(runtime,agent,False,'VISION_FAILED')
            if agent.retries>3:raise SceneFailure('VISION_FAILED','视觉失败超过3次重试，停止')
            return None
        runtime.log('perception','九星模拟图像识别完成',dict(before_state,observation_id=before_id))
        started=time.perf_counter();decision=None;after=None;after_id=None;events=[];error=None;latency=0
        try:
            decision=agent.decide(before);latency=(time.perf_counter()-started)*1000
            runtime.state=decision.state
            runtime.stability.check(runtime,decision.state)
            runtime.stability.checkpoint(runtime,'ACTION_INTENT',before,decision)
            timeline['intent_committed']=time.monotonic()
            if runtime.stop_requested:return None
            action_began=time.monotonic();events=agent.act(world.act,decision)
            action_elapsed=time.monotonic()-action_began
            for e in events:runtime.log(e['type'],e['message'],e['details'])
            after_id,after,after_state=repo.observe(runtime.run_id,world.observation_frame(),agent)
            timeline['after_committed']=time.monotonic()
            if action_elapsed>runtime.action_timeout:raise SceneFailure('ACTION_TIMEOUT','动作超时')
            if time.monotonic()-pipeline_began>10:raise SceneFailure('OBSERVATION_TIMEOUT','观察与证据持久化链路超过10秒，停止')
            if after is None:raise SceneFailure('VISION_FAILED','动作后视觉验证失败')
            valid=agent.verify(before,after,decision,events)
            timeline['validation_finished']=time.monotonic()
            if not valid:
                timeline['recovery_started']=time.monotonic()
                error=next((e['details'].get('error_code') for e in events if e['details'].get('error_code')),'VERIFY_FAILED')
                runtime.state='RECOVER';runtime.log('recover','结果未验证，下一次从新图像重选目标与路径',{'retry':agent.retries,'error_code':error},'WARN')
            else:runtime.log('verify','九星动作实际结果验证通过',{'action':decision.kind,'observation_id':after_id})
            runtime.stability.result(runtime,agent,valid,error)
            runtime.stability.checkpoint(runtime,'VERIFIED' if valid else 'UNCONFIRMED',after,decision)
            timeline['checkpoint_committed']=time.monotonic()
            runtime.last_decision=repo.decision(runtime.run_id,before_id,before,decision,after_id,after,events,latency,error,timeline=timeline)
            return after
        except Exception as exc:
            if after_id is None:
                after_id,after,_=repo.observe(runtime.run_id,world.observation_frame(),agent)
            code=getattr(exc,'code','RECOVERY_EXHAUSTED' if agent.retries>3 else 'ACTION_FAILED')
            timeline['failure_at']=time.monotonic()
            runtime.last_decision=repo.decision(runtime.run_id,before_id,before,decision,after_id,after,events,latency if decision else (time.perf_counter()-started)*1000,code,timeline=timeline)
            if isinstance(exc,SceneFailure):raise
            raise SceneFailure(code,str(exc)) from exc
