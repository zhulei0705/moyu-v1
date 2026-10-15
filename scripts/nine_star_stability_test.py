"""Real simulator workers on isolated SQLite. Scheduling delay is explicit."""
import argparse
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.main import create_app
from backend.research_scene import SCENE_ID
from scripts.service_common import memory_bytes


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--rounds',type=int,required=True)
    parser.add_argument('--tick',type=float,default=.08)
    parser.add_argument('--label',required=True)
    args=parser.parse_args()
    directory=ROOT/'reports'/args.label
    directory.mkdir(exist_ok=False)
    path=directory/'agent.sqlite3'
    with sqlite3.connect(ROOT/'reports/v13-baseline/agent.sqlite3') as src,sqlite3.connect(path) as dst:
        src.backup(dst)
    app=create_app(path,tick=args.tick);r=app.state.runtime
    started_at=datetime.now(timezone.utc).isoformat()
    began=time.monotonic();cpu=time.process_time();samples=[]
    r.start(args.rounds,seed=20261012,scene_id=SCENE_ID)
    deadline=began+args.rounds*190
    while r.thread.is_alive() and time.monotonic()<deadline:
        r.thread.join(.5)
        samples.append({'elapsed':time.monotonic()-began,'memory_bytes':memory_bytes(),
                        'cpu_seconds':time.process_time()-cpu})
    if r.thread.is_alive():r.control('emergency-stop')
    runs=r.store.query('SELECT r.* FROM runs r JOIN research_run x ON x.run_id=r.id ORDER BY r.started')
    results=[]
    for run in runs:
        rid=run['id'];logs=r.store.query('SELECT * FROM logs WHERE runId=? ORDER BY id',(rid,))
        settlement=r.store.query('SELECT * FROM research_settlement WHERE run_id=? ORDER BY stage_id',(rid,))
        total=next((s for s in settlement if s['stage_id']==0),None)
        loot={k:sum(e['details']['reward'][k] for e in logs if e['eventType']=='loot') for k in ('gold','shards','materials')}
        consistent=(total is not None and len(settlement)==5 and loot==run['rewards'] and
                    loot==dict(zip(('gold','shards','materials'),(total['simulated_gold'],total['simulated_fragments'],total['simulated_materials']))) and
                    all(sum(s[k] for s in settlement if s['stage_id']>0)==total[k] for k in ('simulated_gold','simulated_fragments','simulated_materials','consumable_cost')))
        stages={e['details'].get('stage_id') for e in logs if e['eventType'] in ('enter','stage')}
        failures=[{'id':e['id'],'event':e['eventType'],'details':e['details']} for e in logs if e['level'] in ('WARN','ERROR')]
        results.append({'run_id':rid,'status':run['status'],'duration_seconds':run['duration'],
                        'logs':len(logs),'actions':r.store.query('SELECT COUNT(*) AS n FROM agent_decision WHERE run_id=?',(rid,))[0]['n'],
                        'stages':sorted(stages),'consistency':consistent,'failures':failures})
    complete=sum(x['status']=='COMPLETED' for x in results)
    failed=sum(x['status']!='COMPLETED' for x in results)
    integrity=r.store.query('PRAGMA integrity_check')[0]['integrity_check']
    elapsed=time.monotonic()-began;cpu_seconds=time.process_time()-cpu
    result={'started_at_utc':started_at,'recorded_at_utc':datetime.now(timezone.utc).isoformat(),'execution_environment':'SIMULATOR',
            'requested_rounds':args.rounds,'actual_rounds':len(results),'completed':complete,'failed':failed,
            'tick_seconds':args.tick,'wall_seconds':elapsed,'cpu_seconds':cpu_seconds,
            'cpu_percent_one_core_average':100*cpu_seconds/elapsed,
            'average_round_seconds':sum(x['duration_seconds'] for x in results)/len(results) if results else None,
            'success_rate':complete/len(results) if results else None,
            'exception_count':sum(len(x['failures']) for x in results),
            'automatic_recovery_rate':None,'manual_interventions':int(r.stability.review_required),
            'log_count':sum(x['logs'] for x in results),'integrity_check':integrity,
            'database_bytes':path.stat().st_size,'database_consistency':all(x['consistency'] for x in results),
            'runs':results,'samples':samples,'runtime_error':r.error,
            'passed':len(results)==args.rounds and complete==args.rounds and integrity=='ok' and all(x['consistency'] and x['stages']==[1,2,3,4] for x in results)}
    evidence=directory/'result.json';evidence.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('passed','actual_rounds','completed','failed','wall_seconds','average_round_seconds','log_count','database_consistency')},ensure_ascii=False))
    r.store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    r.store.db.close();app.state.knowledge.db.close();app.state.observation.db.close()
    return 0 if result['passed'] else 1


if __name__=='__main__':sys.exit(main())
