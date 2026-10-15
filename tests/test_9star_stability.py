import sqlite3
import time
import pytest
from backend.main import create_app
from backend.research_scene import SCENE_ID, SceneFailure
from backend.agent import Decision
from backend.research_agent import ResearchAgent


def app_at(path):
    with sqlite3.connect('reports/v13-baseline/agent.sqlite3') as src, sqlite3.connect(path) as dst:
        src.backup(dst)
    return create_app(path, tick=.001)


@pytest.mark.parametrize('fault', ['advance_persistent','path_persistent'])
def test_persistent_fault_stops_without_success(tmp_path, fault):
    r=app_at(tmp_path/'fault.sqlite').state.runtime
    r.start(scene_id=SCENE_ID,fault=fault)
    r.thread.join(20)
    assert not r.thread.is_alive() and r.completed_session==0
    assert r.store.query('SELECT status FROM runs WHERE id=?',(r.run_id,))[0]['status']=='FAILED'
    assert r.store.query('SELECT success FROM research_settlement WHERE run_id=? AND stage_id=0',(r.run_id,))[0]['success']==0


@pytest.mark.parametrize('persistent', [False,True])
def test_database_failure_always_releases_worker(tmp_path, persistent):
    r=app_at(tmp_path/'db.sqlite').state.runtime
    original=r.store.execute
    failures=[]
    def write(sql,args=()):
        if 'agent_observation' in sql or (persistent and failures):
            failures.append(sql)
            raise sqlite3.OperationalError('injected database write failure')
        return original(sql,args)
    r.store.execute=write
    r.start(scene_id=SCENE_ID)
    r.thread.join(20)
    assert not r.thread.is_alive() and not r.paused and r.error
    assert r.completed_session==0
    r.store.execute=original
    if persistent:
        assert r.status()['stability']['manual_review_required']
        with pytest.raises(ValueError):r.start(scene_id=SCENE_ID)
        second=create_app(tmp_path/'db.sqlite').state.runtime
        assert second.store.query('SELECT status FROM runs WHERE id=?',(r.run_id,))[0]['status']=='INTERRUPTED'
        assert not second.store.query('SELECT * FROM research_settlement WHERE run_id=? AND success=1',(r.run_id,))
    else:
        assert r.store.query('SELECT status FROM runs WHERE id=?',(r.run_id,))[0]['status']=='FAILED'


def test_interrupted_research_cannot_be_promoted_to_success(tmp_path):
    r=app_at(tmp_path/'settle.sqlite').state.runtime
    r.start(scene_id=SCENE_ID);r.thread.join(20)
    assert r.completed_session==1
    r.store.execute('DELETE FROM research_settlement WHERE run_id=?',(r.run_id,))
    r.store.execute("UPDATE runs SET status='INTERRUPTED' WHERE id=?",(r.run_id,))
    with pytest.raises(SceneFailure,match='仅运行中'):
        r.research.settle(r.run_id,1,True,r.world)


def test_attack_does_not_confirm_player_damage_alone(tmp_path):
    from backend.research_scene import NineStarResearchDungeon
    r=app_at(tmp_path/'attack.sqlite').state.runtime
    w=NineStarResearchDungeon(r.profile_factory());w.act('enter')
    a=ResearchAgent();before=a.observe(w.render());w.hp-=2;after=a.observe(w.render())
    target=next(iter(before.monsters))
    assert not a.verify(before,after,Decision('COMBAT','attack',target),
                        [{'type':'attack','details':{'target':target}}])


def test_state_timeout_no_progress_and_storage_guards(tmp_path):
    r=app_at(tmp_path/'bounds.sqlite').state.runtime
    s=r.stability;s.check(r,'NAVIGATE');s.state_began-=61
    with pytest.raises(SceneFailure,match='状态活动'):s.check(r,'NAVIGATE')
    s.state=None;s.last_progress=time.monotonic()-21
    with pytest.raises(SceneFailure,match='无已确认动作'):s.check(r,'SEARCH')
    r.store.max_database_bytes=1
    with pytest.raises(SceneFailure,match='容量上限'):r.start(scene_id=SCENE_ID)


def test_checkpoint_and_metrics_reference_actual_evidence(tmp_path):
    r=app_at(tmp_path/'checkpoint.sqlite').state.runtime
    r.start(scene_id=SCENE_ID,fault='path_blocked');r.thread.join(20)
    assert r.completed_session==1
    status=r.status()['stability']
    assert status['heartbeat_at'] and status['last_success_at']
    assert status['observation']['exited'] and status['recoveries'][0]['recovered_at']
    rows=r.store.query('SELECT * FROM agent_checkpoint WHERE run_id=?',(r.run_id,))
    assert rows[0]['status']=='VERIFIED' and rows[0]['payload']['observation']['exited']


def test_stop_completion_before_control_lock_is_accepted(tmp_path):
    r=app_at(tmp_path/'stop.sqlite').state.runtime
    r.tick=1
    r.start(scene_id=SCENE_ID)
    deadline=time.monotonic()+5
    while not r.world and time.monotonic()<deadline:time.sleep(.01)
    original=r.wake.set
    def finish_before_lock():
        original()
        r.thread.join(5)
    r.wake.set=finish_before_lock
    r.control('emergency-stop')
    assert not r.thread.is_alive() and r.state=='STOPPED'


def test_observation_storage_time_is_separate_from_action_timeout(tmp_path):
    from backend.research_scene import NineStarResearchDungeon
    from backend.store import now
    r=app_at(tmp_path/'timing.sqlite').state.runtime
    r.profile=r.profile_factory();r.world=NineStarResearchDungeon(r.profile)
    r.run_id='timing'
    r.store.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)',('timing','RUNNING',now(),None,None,1,None,None))
    r.research.bind(r.run_id,r.profile)
    r.action_timeout=.01
    original=r.research.observe
    def slow_evidence(*args):
        time.sleep(.03)
        return original(*args)
    r.research.observe=slow_evidence
    after=r.research_controller.step(r,ResearchAgent())
    assert after.entered and r.last_decision['error_code'] is None


def test_corrupt_marker_reobserved_and_stale_worker_latches_stop(tmp_path):
    from backend.research_scene import NineStarResearchDungeon
    r=app_at(tmp_path/'confidence.sqlite').state.runtime
    w=NineStarResearchDungeon(r.profile_factory());a=ResearchAgent()
    frame=w.render();x,y=w.player;frame[y*24+2,x*24+2]=(0,0,0)
    with pytest.raises(ValueError,match='标记不完整'):a.observe(frame)
    class Alive:
        def is_alive(self):return True
    r.thread=Alive();r.stability.pulse_time=time.monotonic()-31
    r.stability.detect_stall(r)
    assert r.stop_requested and r.stability.review_required and 'STALE' in r.error


def test_loot_event_write_failure_cannot_settle_partial_inventory(tmp_path):
    r=app_at(tmp_path/'lost-loot.sqlite').state.runtime
    original=r.store.log
    def lose_loot(run_id,stage,level,event,message,details=None):
        if event=='loot':raise sqlite3.OperationalError('injected loot event write failure')
        return original(run_id,stage,level,event,message,details)
    r.store.log=lose_loot
    r.start(scene_id=SCENE_ID);r.thread.join(20)
    assert not r.thread.is_alive() and r.world.inventory['materials']>0
    assert r.stability.review_required
    assert not r.store.query('SELECT * FROM research_settlement WHERE run_id=?',(r.run_id,))
    assert r.store.query('SELECT status FROM runs WHERE id=?',(r.run_id,))[0]['status']=='FAILED'
