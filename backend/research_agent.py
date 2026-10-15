"""与原Agent共用图像观察与A*；没有World真值输入。"""
from .agent import Agent,Decision,astar
from .research_scene import SceneFailure

class ResearchAgent(Agent):
    def __init__(self,stage_metadata=None):
        super().__init__()
        self.stage_metadata=stage_metadata or []

    def decide(self,obs):
        if obs.confidence is None or obs.confidence<0.9:
            raise SceneFailure('LOW_CONFIDENCE','视觉可靠性不足，不执行动作')
        if obs.hp<=0:raise SceneFailure('CHARACTER_DEAD','观察到角色死亡，终止任务')
        reason='';route=[];goal=None
        if not obs.entered:
            if not obs.entries or not obs.bag_capacity:raise SceneFailure('ELIGIBILITY_FAILED','未识别到入口或有效背包容量，不能进入模拟副本')
            if obs.player not in obs.entries:
                route=self.route(obs,obs.entries);decision=Decision('NAVIGATE','move',route[1]);reason='模拟角色存活但未在入口，A*前往图像识别的入口'
            else:decision=Decision('ENTER','enter');reason='模拟角色存活、背包有效，当前位置为可见入口，执行进入；不判断未知官服资格'
        elif obs.loot and obs.bag_capacity and obs.bag_used>=obs.bag_capacity:
            decision=Decision('RECOVER','deposit');reason='像素HUD显示背包已满，先按模拟仓库规则释放空间';goal={'kind':'INVENTORY_SPACE'}
        elif obs.monsters:
            adjacent=sorted(p for p in obs.monsters if abs(p[0]-obs.player[0])+abs(p[1]-obs.player[1])<=1)
            boss_adjacent=[p for p in adjacent if p in obs.bosses]
            if adjacent:
                target=(boss_adjacent or adjacent)[0];decision=Decision('COMBAT','attack',target)
                goal={'kind':'BOSS' if target in obs.bosses else 'GUARD','position':target};reason='视觉识别到相邻首领/怪物，满足模拟近战距离；攻击后复查生命和目标'
            else:
                targets=obs.bosses or obs.monsters
                goals={(x+dx,y+dy) for x,y in targets for dx,dy in [(1,0),(-1,0),(0,1),(0,-1)]}
                route=self.route(obs,goals);decision=Decision('NAVIGATE','move',route[1]);goal={'kind':'APPROACH_BOSS' if obs.bosses else 'APPROACH_GUARD','position':route[-1]};reason='按当前图像障碍运行A*，选择可达目标邻格，逐格执行并复查'
        elif obs.loot:
            if obs.player in obs.loot:decision=Decision('LOOT','loot');reason='视觉识别到脚下掉落且有背包空间，实际拾取后检查资源变化';goal={'kind':'LOOT','position':obs.player}
            else:
                route=self.route(obs,obs.loot);decision=Decision('NAVIGATE','move',route[1]);reason='当前阶段无可见敌人，A*到达可见掉落位置';goal={'kind':'LOOT','position':route[-1]}
        elif not obs.complete:
            decision=Decision('VERIFY','advance');reason='当前图像无敌人和掉落，申请环境验证清场并推进；不使用定时器切阶段';goal={'kind':'STAGE_CLEAR','stage_id':obs.stage}
        elif obs.player in obs.exits:
            decision=Decision('SETTLEMENT','exit');reason='四阶段清场标志成立且角色在可见出口，退出后验证实际结算';goal={'kind':'EXIT','position':obs.player}
        else:
            route=self.route(obs,obs.exits);decision=Decision('NAVIGATE','move',route[1]);reason='四阶段完成，A*前往图像识别的已开放出口';goal={'kind':'EXIT','position':route[-1]}
        decision.reason=reason;decision.goal=goal or {'kind':decision.kind.upper()};decision.route=route
        if 1<=obs.stage<=len(self.stage_metadata):
            decision.goal.update(stage_id=obs.stage,stage_label=self.stage_metadata[obs.stage-1]['boss_name'],label_basis='SIMULATOR_SCENE_METADATA',label_assignment_class='SIMULATED_ASSUMPTION')
        return decision

    def route(self,obs,goals):
        paths=[astar(obs.player,g,obs.walls|obs.monsters,obs.width,obs.height) for g in sorted(goals)]
        paths=[p for p in paths if len(p)>1]
        if not paths:raise SceneFailure('NO_PATH','A*未找到目标可达路径')
        return min(paths,key=lambda p:(len(p),p))

    def verify(self,before,after,decision,events):
        if after.stage < before.stage or after.stage > before.stage+1:
            raise SceneFailure('INVALID_TRANSITION','阶段变化不合法')
        if decision.kind=='attack':
            target=decision.target
            hit=any(e['type']=='attack' and tuple(e['details'].get('target',()))==target for e in events)
            damage=target in before.enemy_hp and target in after.enemy_hp and after.enemy_hp[target]<before.enemy_hp[target]
            killed=target in before.monsters and target not in after.monsters and target in after.loot and any(e['type']=='defeat' for e in events)
            valid=hit and (damage or killed)
            self.retries=0 if valid else self.retries+1
            if self.retries>3:raise SceneFailure('RECOVERY_EXHAUSTED','攻击结果未证实，停止')
            return valid
        if decision.kind=='advance' and (before.monsters or before.loot or not before.entered):
            raise SceneFailure('INVALID_TRANSITION','未清场不能推进阶段')
        if decision.kind=='exit' and not (before.complete and after.exited and after.inventory==before.inventory):
            raise SceneFailure('INVALID_TRANSITION','退出证据或资源守恒不成立')
        if decision.kind=='deposit':
            valid=any(e['type']=='deposit' for e in events) and after.bag_used<before.bag_used
            self.retries=0 if valid else self.retries+1
            if self.retries>3:raise SceneFailure('RECOVERY_EXHAUSTED','转存验证失败超过3次重试')
            return valid
        valid=super().verify(before,after,decision,events)
        if decision.kind=='loot' and valid:
            rewards=[e['details']['reward'] for e in events if e['type']=='loot']
            if before.inventory is None or after.inventory is None or not rewards or any(after.inventory[k]-before.inventory[k]!=sum(r[k] for r in rewards) for k in before.inventory):raise SceneFailure('LOOT_MISMATCH','拾取像素资源增量与事件不一致')
        return valid
