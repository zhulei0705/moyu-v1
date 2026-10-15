"""Bounded simulator execution. Checkpoints are evidence, never replay instructions."""
import json
import time
from collections import deque
from .store import now
from .research_scene import SceneFailure


class Stability:
    budgets = {'PREPARE':10, 'QUALIFY':10, 'ENTER':10, 'SEARCH':15,
               'NAVIGATE':60, 'COMBAT':30, 'LOOT':15, 'VERIFY':10,
               'SETTLEMENT':10, 'RECOVER':15}

    def __init__(self, store):
        self.store = store
        store.execute('''CREATE TABLE IF NOT EXISTS agent_checkpoint(
          run_id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, status TEXT NOT NULL,
          payload TEXT NOT NULL)''')
        self.reset()

    def reset(self):
        self.heartbeat = None
        self.pulse_time = None
        self.last_success = None
        self.last_progress = time.monotonic()
        self.state = None
        self.state_began = self.last_progress
        self.retries = 0
        self.observation = None
        self.goal = None
        self.recoveries = deque(maxlen=50)
        self.cleanup_errors = []
        self.review_required = False
        self.pause_offset = 0

    def pulse(self):
        self.heartbeat = now()
        self.pulse_time = time.monotonic()

    def detect_stall(self, runtime):
        if runtime.thread and runtime.thread.is_alive() and self.pulse_time is not None and time.monotonic()-self.pulse_time>30:
            self.review_required=True
            runtime.stop_requested=True
            runtime.wake.set()
            runtime.error='WORKER_HEARTBEAT_STALE：超过30秒未响应，需要人工审核'

    def check(self, runtime, state):
        current = time.monotonic() - runtime.pause_total
        if state != self.state:
            self.state, self.state_began = state, current
        if current - self.state_began > self.budgets.get(state, 15):
            raise SceneFailure('STATE_TIMEOUT', '状态活动时间超限：'+state)
        if current - self.last_progress > 20:
            raise SceneFailure('NO_PROGRESS', '20秒无已确认动作，安全停止')

    def checkpoint(self, runtime, status, observation=None, decision=None):
        from .research_repository import perception
        if observation is not None:
            self.observation = perception(observation)
        if decision is not None:
            self.goal = getattr(decision, 'goal', {'kind':decision.kind})
        payload = {'observation':self.observation, 'goal':self.goal,
                   'action':decision.kind if decision else None,
                   'policy':'STOP_UNCONFIRMED_NO_REPLAY', 'source':'SIMULATOR'}
        self.store.execute('''INSERT INTO agent_checkpoint VALUES(?,?,?,?)
          ON CONFLICT(run_id) DO UPDATE SET updated_at=excluded.updated_at,
          status=excluded.status,payload=excluded.payload''',
          (runtime.run_id, now(), status, json.dumps(payload,ensure_ascii=False)))

    def result(self, runtime, agent, valid, error=None):
        self.retries = agent.retries
        if valid:
            if self.recoveries and self.recoveries[-1]['recovered_at'] is None:
                self.recoveries[-1]['recovered_at'] = now()
            self.last_success = now()
            self.last_progress = time.monotonic() - runtime.pause_total
        else:
            self.recoveries.append({'at':now(), 'run_id':runtime.run_id,
                                    'error':error, 'retry':agent.retries, 'recovered_at':None})

    def storage_guard(self):
        self.store.evidence.guard()
        # Preserve all historical evidence; stop before bounded new growth exceeds quota.
        with self.store.lock:
            pages=self.store.db.execute('PRAGMA page_count').fetchone()[0]
            size=self.store.db.execute('PRAGMA page_size').fetchone()[0]
        if pages*size >= self.store.max_database_bytes:
            self.store.evidence.last_stop='STORAGE_LIMIT'
            self.store.evidence.persist_monitor()
            raise SceneFailure('STORAGE_LIMIT', '证据数据库达到容量上限，需要人工归档')

    def status(self, runtime):
        current=time.monotonic()-runtime.pause_total
        if runtime.paused and runtime.pause_began:
            current-=time.monotonic()-runtime.pause_began
        return {'heartbeat_at':self.heartbeat, 'last_success_at':self.last_success,
                'heartbeat_age_seconds':time.monotonic()-self.pulse_time if self.pulse_time is not None else None,
                'retry_count':self.retries, 'stuck_seconds':max(0,current-self.last_progress) if runtime.thread and runtime.thread.is_alive() else 0,
                'observation':self.observation, 'goal':self.goal,
                'recoveries':list(self.recoveries), 'manual_review_required':self.review_required,
                'cleanup_errors':list(self.cleanup_errors), 'state_timeouts':self.budgets,
                'database_limit_bytes':self.store.max_database_bytes}
