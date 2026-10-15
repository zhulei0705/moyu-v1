import os,sys,sqlite3,time,json,collections
from pathlib import Path
root=Path.cwd();home=root/'reports/v133-frequency';home.mkdir(exist_ok=False)
os.environ['MOYU_DB']=str(home/'import.sqlite3');sys.path.insert(0,str(root))
from backend.main import create_app
from backend.research_scene import SCENE_ID
path=home/'agent.sqlite3'
with sqlite3.connect(root/'reports/v13-baseline/agent.sqlite3') as s,sqlite3.connect(path) as t:s.backup(t)
app=create_app(path,tick=.08);r=app.state.runtime;db=r.store.db
initial=db.execute('pragma page_count').fetchone()[0]*db.execute('pragma page_size').fetchone()[0]
counts=collections.Counter()
def trace(sql):
 operation=sql.split()[0].upper()
 if operation in ('INSERT','UPDATE','BEGIN','COMMIT'):
  counts[operation]+=1
  if operation in ('INSERT','UPDATE'):
   for name in ['agent_observation','evidence_blob','evidence_frame_ref','agent_decision','logs','agent_checkpoint','agent_state_history','runs','research_settlement']:
    if name in sql:counts[name]+=1
  
db.set_trace_callback(trace);began=time.monotonic();r.start(3,seed=20261012,scene_id=SCENE_ID);r.thread.join(180)
elapsed=time.monotonic()-began;db.set_trace_callback(None)
status=r.store.evidence.status(force=True)
result=dict(wall_seconds=elapsed,rounds=r.completed_session,error=r.error,initial_bytes=initial,counts=dict(counts),
 commits_per_minute=counts['COMMIT']*60/elapsed,statements_per_minute=sum(counts[k] for k in ['INSERT','UPDATE'])*60/elapsed,
 growth_bytes_per_minute=(status['logical_bytes']-initial)*60/elapsed,bytes_per_round=(status['logical_bytes']-initial)/max(1,r.completed_session),storage=status)
(home/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
for repo in [r.store,app.state.knowledge,app.state.observation]:repo.db.close()
