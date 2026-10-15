"""原创 SIMULATED 世界；动作事件是资源和结算的唯一来源。"""
from dataclasses import dataclass
import random
import cv2
import numpy as np

TILE = 24
COLORS = {'wall': (55, 55, 65), 'player': (255, 180, 20), 'monster': (30, 30, 240), 'loot': (20, 220, 240), 'exit': (200, 80, 180), 'entry': (80, 150, 50)}
STAGE_NAMES = ['等待进入', '铁甲守门者', '熔火双卫', '暗影巡猎者', '星核守护者']


class World:
    width, height = 20, 14

    def __init__(self, seed=1, fault=None):
        self.rng = random.Random(seed)
        self.fault = fault
        self.fault_used = False
        self.reset()

    def reset(self):
        self.walls = {(x, y) for x in range(self.width) for y in range(self.height)
                      if x in (0, self.width-1) or y in (0, self.height-1)}
        self.walls |= {(7, y) for y in range(2, 11) if y != 6}
        self.walls |= {(13, y) for y in range(3, 12) if y != 9}
        self.player = (2, 2)
        self.entry = (2, 2)
        self.boss = None
        self.hp = 100
        self.attack = 25
        self.stage = 0
        self.monsters = {}
        self.loot = {}
        self.inventory = {'gold': 0, 'shards': 0, 'materials': 0}
        self.entered = False
        self.complete = False
        self.exited = False
        self.exit = (17, 11)
        self.actions = 0

    def spawn(self):
        positions = [(5, 3), (10, 5), (16, 4), (16, 10)]
        x, y = positions[self.stage-1]
        self.boss = (x,y)
        self.monsters = {(x, y): 75, (x+1, y): 50}

    def act(self, kind, target=None):
        self.actions += 1
        if self.fault == 'crash' and not self.fault_used:
            self.fault_used = True
            raise RuntimeError('注入 Worker 异常')
        if self.fault == 'stuck' and not self.fault_used and kind == 'move':
            self.fault_used = True
            return [{'type': 'blocked', 'message': '注入一次移动阻塞', 'details': {}}]
        events = []
        def emit(t, msg, **details):
            events.append({'type': t, 'message': msg, 'details': details})
        if kind == 'enter' and not self.entered and self.player == self.entry:
            self.entered = True
            self.stage = 1
            self.spawn()
            emit('enter', '进入原创四阶段测试副本', stage=self.stage)
        elif kind == 'move':
            if target is None or abs(target[0]-self.player[0])+abs(target[1]-self.player[1]) != 1:
                raise ValueError('移动必须是相邻格')
            if target in self.walls or target in self.monsters:
                emit('blocked', '路径被阻挡')
            else:
                self.player = target
                emit('move', '移动到下一格', position=target)
        elif kind == 'attack':
            if target in self.monsters and self.distance(target) <= 1:
                self.monsters[target] -= self.attack
                self.hp -= 2
                emit('attack', '攻击目标', damage=self.attack, target=target, hp=self.hp)
                if self.monsters[target] <= 0:
                    del self.monsters[target]
                    reward = {'gold': self.rng.randint(40, 80), 'shards': self.rng.randint(1, 3), 'materials': 1}
                    self.loot[target] = reward
                    emit('defeat', '击败怪物，生成 SIMULATED 掉落', reward=reward)
        elif kind == 'loot' and self.player in self.loot:
            reward = self.loot.pop(self.player)
            for key, value in reward.items():
                self.inventory[key] += value
            emit('loot', '拾取 SIMULATED 资源', reward=reward)
        elif kind == 'advance' and self.entered and not self.monsters and not self.loot:
            if self.stage < 4:
                self.stage += 1
                self.spawn()
                emit('stage', '开启下一首领阶段', stage=self.stage)
            elif not self.complete:
                self.complete = True
                emit('complete', '四阶段验证完成，出口已开放')
        elif kind == 'exit' and self.complete and self.player == self.exit:
            self.exited = True
            emit('exit', '已通过出口离开副本', inventory=self.inventory.copy())
        if self.hp <= 0:
            raise RuntimeError('角色生命耗尽')
        return events

    def distance(self, target):
        return abs(self.player[0]-target[0])+abs(self.player[1]-target[1])

    def render(self):
        frame = np.full((self.height*TILE, self.width*TILE, 3), (24, 22, 18), np.uint8)
        for x in range(self.width):
            for y in range(self.height):
                cv2.rectangle(frame, (x*TILE, y*TILE), ((x+1)*TILE-1, (y+1)*TILE-1), (36, 34, 30), 1)
        def draw(pos, color):
            x, y = pos
            cv2.rectangle(frame, (x*TILE+3, y*TILE+3), (x*TILE+TILE-4, y*TILE+TILE-4), color, -1)
        for pos in self.walls: draw(pos, COLORS['wall'])
        draw(self.entry, COLORS['entry'])
        if self.complete: draw(self.exit, COLORS['exit'])
        for pos in self.loot: draw(pos, COLORS['loot'])
        for pos in self.monsters: draw(pos, COLORS['monster'])
        # 角色使用独立角标，脚下掉落/出口保持可见，避免视觉遮挡造成反复寻路。
        x, y = self.player
        cv2.rectangle(frame, (x*TILE+2,y*TILE+2), (x*TILE+9,y*TILE+9), COLORS['player'], -1)
        # 图像顶部 HUD 像素条编码阶段、生命、入口和通关标识；感知只读图像。
        for i, value in enumerate([self.stage, self.hp, int(self.entered), int(self.complete), int(self.exited)]):
            frame[0:2, i*3:i*3+2] = (value, 0, 0)
        return frame
