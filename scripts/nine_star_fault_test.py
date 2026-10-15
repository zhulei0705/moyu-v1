import json
import sqlite3
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.main import create_app
from backend.research_scene import SCENE_ID
from scripts.service_common import memory_bytes

faults={'target_disappeared':True,'path_blocked':True,'vision_failed':True,
        'loot_failed':True,'bag_full':True,'character_dead':False,
        'vision_persistent':False,'stage_timeout':False,'loot_persistent':False,
        'scene_load_failed':False,'worker_interrupted':False,
        'advance_persistent':False,'path_persistent':False}

def main():
    directory=ROOT/'reports/9star-faults';directory.mkdir(exist_ok=False)
    results=[]
    for fault,expected in faults.items():
        path=directory/(fault+'.sqlite3')
        with sqlite3.connect(ROOT/'reports/v13-baseline/agent.sqlite3') as src,sqlite3.connect(path) as dst:src.backup(dst)
        app=create_app(path,tick=.001);r=app.state.runtime
        start=time.monotonic();cpu=time.process_time();samples=[]
        r.start(scene_id=SCENE_ID,fault=fault)
        while r.thread.is_alive() and time.monotonic()-start<30:
            r.thread.join(.1);samples.append(memory_bytes())
        if r.thread.is_alive():r.control('emergency-stop')
        logs=r.store.query('SELECT * FROM logs WHERE runId=? ORDER BY id',(r.run_id,))
        run=r.store.query('SELECT * FROM runs WHERE id=?',(r.run_id,))[0]
        settlement=r.store.query('SELECT * FROM research_settlement WHERE run_id=? AND stage_id=0',(r.run_id,))
        total=settlement[0] if settlement else None
        loot={k:sum(e['details']['reward'][k] for e in logs if e['eventType']=='loot') for k in ('gold','shards','materials')}
        consistent=total is not None and loot==dict(zip(('gold','shards','materials'),(total['simulated_gold'],total['simulated_fragments'],total['simulated_materials'])))
        result={'fault':fault,'run_id':r.run_id,'status':run['status'],'seconds':time.monotonic()-start,
                'completed':r.completed_session,'failed':int(run['status']=='FAILED'),'success_rate':r.completed_session,
                'cpu_seconds':time.process_time()-cpu,'memory_bytes_samples':samples,
                'log_count':len(logs),'error':r.error,'manual_review_required':r.stability.review_required,
                'recovery_attempts':list(r.stability.recoveries),'database_consistency':consistent,
                'events':[{'id':e['id'],'event':e['eventType'],'details':e['details']} for e in logs if e['level'] in ('WARN','ERROR') or e['eventType'] in ('deposit','target_lost','blocked')],
                'passed':not r.thread.is_alive() and bool(r.completed_session)==expected and consistent and bool(total['success'])==expected}
        results.append(result)
        print(json.dumps({'fault':fault,'status':run['status'],'passed':result['passed']}),flush=True)
        r.store.db.close();app.state.observation.db.close();app.state.knowledge.db.close()
    (directory/'result.json').write_text(json.dumps({'passed':all(x['passed'] for x in results),'cases':results},ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if all(x['passed'] for x in results) else 1

if __name__=='__main__':sys.exit(main())
