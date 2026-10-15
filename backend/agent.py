from dataclasses import dataclass, field
import heapq
import cv2
import numpy as np
from .world import TILE, COLORS

STATES = ['IDLE','PREPARE','QUALIFY','ENTER','SEARCH','NAVIGATE','COMBAT','LOOT','VERIFY','SETTLEMENT','RECOVER','STOPPED']


@dataclass
class Observation:
    player: tuple
    walls: set
    monsters: set
    loot: set
    exits: set
    stage: int
    hp: int
    entered: bool
    complete: bool
    exited: bool
    width: int
    height: int
    bosses: set = field(default_factory=set)
    entries: set = field(default_factory=set)
    inventory: dict | None = None
    bag_used: int = 0
    bag_capacity: int = 0
    consumable_cost: int = 0
    enemy_hp: dict = field(default_factory=dict)
    confidence: float | None = None


class Vision:
    def observe(self, frame):
        """独立图像感知：颜色分割及连通区域检测，无世界对象或真值输入。"""
        found = {}
        for name, color in COLORS.items():
            mask = cv2.inRange(frame, np.array(color, np.uint8), np.array(color, np.uint8))
            count, _, stats, centers = cv2.connectedComponentsWithStats(mask)
            if name=='player':
                candidates=[i for i in range(1,count) if stats[i,cv2.CC_STAT_AREA]>=20]
                if len(candidates)!=1 or tuple(stats[candidates[0],2:5])!=(8,8,64):
                    raise ValueError('视觉角色标记不完整，置信校验不足')
            found[name] = {(int(cx)//TILE, int(cy)//TILE) for i, (cx, cy) in enumerate(centers)
                           if i and stats[i, cv2.CC_STAT_AREA] >= 20}
        if len(found['player']) != 1:
            raise ValueError('视觉未识别到唯一角色')
        hud = [int(frame[0, i*3, 0]) for i in range(5)]
        if hud[0] not in range(5) or any(v not in (0,1) for v in hud[2:]):
            raise ValueError('视觉HUD不可靠')
        if any(tuple(frame[1,i*3+1])!=(v,0,0) for i,v in enumerate(hud)):
            raise ValueError('视觉HUD重复像素不一致')
        observation = Observation(next(iter(found['player'])), found['wall'], found['monster'], found['loot'],
                           found['exit'], hud[0], hud[1], bool(hud[2]), bool(hud[3]), bool(hud[4]),
                           frame.shape[1]//TILE, frame.shape[0]//TILE)
        from .research_scene import BOSS_COLOR
        mask=cv2.inRange(frame,np.array(BOSS_COLOR,np.uint8),np.array(BOSS_COLOR,np.uint8))
        count,_,stats,centers=cv2.connectedComponentsWithStats(mask)
        observation.bosses={(int(cx)//TILE,int(cy)//TILE) for i,(cx,cy) in enumerate(centers) if i and stats[i,cv2.CC_STAT_AREA]>=20}
        observation.monsters |= observation.bosses
        observation.entries=found['entry']
        if tuple(frame[0,15])==(177,13,3):
            values=[int(frame[0,16+i,0])+(int(frame[0,16+i,1])<<8) for i in range(6)]
            observation.inventory=dict(zip(('gold','shards','materials'),values[:3]))
            observation.bag_used,observation.bag_capacity,observation.consumable_cost=values[3:]
            if observation.bag_used > observation.bag_capacity:
                raise ValueError('视觉背包容量不一致')
        if tuple(frame[0,22]) == (178,13,3):
            observation.enemy_hp={p:int(frame[p[1]*TILE+1,p[0]*TILE+1,0]) for p in observation.monsters}
            if any(hp <= 0 for hp in observation.enemy_hp.values()):
                raise ValueError('视觉目标生命不可靠')
        # Deterministic color/HUD validation score, not real-game model probability.
        observation.confidence = 1.0
        return observation


def astar(start, goal, blocked, width, height):
    queue = [(0, start)]
    cost, previous = {start: 0}, {}
    while queue:
        _, current = heapq.heappop(queue)
        if current == goal:
            path = [current]
            while current in previous:
                current = previous[current]
                path.append(current)
            return list(reversed(path))
        for dx, dy in [(1,0),(-1,0),(0,1),(0,-1)]:
            nxt = (current[0]+dx, current[1]+dy)
            if not (0 <= nxt[0] < width and 0 <= nxt[1] < height) or nxt in blocked:
                continue
            new = cost[current]+1
            if new < cost.get(nxt, float('inf')):
                cost[nxt], previous[nxt] = new, current
                heapq.heappush(queue, (new+abs(goal[0]-nxt[0])+abs(goal[1]-nxt[1]), nxt))
    return []


@dataclass
class Decision:
    state: str
    kind: str
    target: tuple | None = None


class Agent:
    def __init__(self):
        self.vision = Vision()
        self.retries = 0

    def observe(self, frame): return self.vision.observe(frame)

    def decide(self, obs):
        if not obs.entered: return Decision('ENTER', 'enter')
        if obs.monsters:
            adjacent = sorted(p for p in obs.monsters if abs(p[0]-obs.player[0])+abs(p[1]-obs.player[1]) <= 1)
            if adjacent: return Decision('COMBAT', 'attack', adjacent[0])
            goals = {(x+dx,y+dy) for x,y in obs.monsters for dx,dy in [(1,0),(-1,0),(0,1),(0,-1)]}
        elif obs.loot:
            if obs.player in obs.loot: return Decision('LOOT', 'loot')
            goals = obs.loot
        elif not obs.complete:
            return Decision('VERIFY', 'advance')
        elif obs.player in obs.exits:
            return Decision('SETTLEMENT', 'exit')
        else:
            goals = obs.exits
        paths = [astar(obs.player, goal, obs.walls|obs.monsters, obs.width, obs.height) for goal in sorted(goals)]
        paths = [p for p in paths if len(p)>1]
        if not paths: raise RuntimeError('未找到可行路径')
        return Decision('NAVIGATE', 'move', min(paths, key=len)[1])

    def act(self, adapter, decision):
        return adapter(decision.kind, decision.target)

    def verify(self, before, after, decision, events):
        valid = bool(events) and not any(e['type']=='blocked' for e in events)
        if decision.kind == 'move': valid = valid and after.player == decision.target and before.player != after.player
        if decision.kind == 'loot': valid = valid and len(after.loot) < len(before.loot)
        if decision.kind == 'attack': valid = valid and after.hp < before.hp
        if decision.kind == 'enter': valid = valid and after.entered
        if decision.kind == 'exit': valid = valid and after.exited
        if decision.kind == 'advance': valid = valid and (after.stage > before.stage or after.complete)
        if not valid:
            self.retries += 1
            if self.retries > 3: raise RuntimeError('动作验证失败超过重试上限')
        else: self.retries = 0
        return valid
