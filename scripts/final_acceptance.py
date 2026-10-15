"""Frozen-source, real-clock final acceptance in a dedicated simulator root."""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import zipfile
import importlib.metadata
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def stamp():return datetime.now(timezone.utc).isoformat()
def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while data:=f.read(1024*1024):h.update(data)
    return h.hexdigest()
def source_files():
    files=[]
    for folder in ('backend','scripts','tests','frontend/src','frontend/tests'):
        files += [p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    files += [ROOT/n for n in ('requirements.txt','requirements-lock.txt','pytest.ini','frontend/package.json','frontend/package-lock.json','frontend/index.html','frontend/vite.config.ts','frontend/tsconfig.json') if (ROOT/n).exists()]
    return {p.relative_to(ROOT).as_posix():digest(p) for p in sorted(set(files))}
def source_hash(files):return hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
def historical_files():
    files=[p for p in (ROOT/'data').rglob('*') if p.is_file()]
    for folder in ('reports/v13-baseline','reports/v1.2-baseline','reports/v1.1-baseline'):
        files += [p for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in ('.sqlite3','.json','.png','.log')]
    for n in ('official-collection.json','knowledge-browser-results.json','v133-1h/result.json','v133-1h/verification.json'):
        if (ROOT/'reports'/n).exists():files.append(ROOT/'reports'/n)
    return {p.relative_to(ROOT).as_posix():{'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted(set(files))}
def write(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def configure(home,db):
    os.environ.update(MOYU_ENV='SIMULATOR_TEST',MOYU_TEST_ROOT=str(home.resolve()),MOYU_DB=str(db.resolve()))
    temp=home/'process-temp';temp.mkdir(exist_ok=True)
    for key in ('TEMP','TMP','TMPDIR'):os.environ[key]=str(temp)
def verify(dbpath):
    with sqlite3.connect(f'file:{dbpath.resolve().as_posix()}?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA cache_size=-65536')
        rows=db.execute('select r.* from runs r join research_run x on x.run_id=r.id order by started').fetchall()
        counts={}
        for r in rows:counts[r['status']]=counts.get(r['status'],0)+1
        integrity=db.execute('pragma integrity_check').fetchone()[0]
        missing=db.execute('select count(*) from agent_observation o left join evidence_frame_ref r on r.observation_id=o.id left join evidence_blob b on b.hash=r.hash where length(o.frame_png)=0 and b.hash is null').fetchone()[0]
        refbad=db.execute('select count(*) from agent_observation o join evidence_frame_ref r on r.observation_id=o.id where o.frame_sha256<>r.hash').fetchone()[0]
        hashbad=sum(hashlib.sha256(png).hexdigest()!=h for h,png in db.execute('select hash,png from evidence_blob'))
        fk=[tuple(r) for r in db.execute('pragma foreign_key_check')]
        decbad=db.execute('select count(*) from agent_decision d left join agent_observation b on b.id=d.observation_id left join agent_observation a on a.id=d.after_observation_id where b.id is null or (d.after_observation_id is not null and a.id is null)').fetchone()[0]
        dup=db.execute('select count(*) from (select run_id,stage_id,count(*) n from research_settlement group by run_id,stage_id having n>1)').fetchone()[0]
        bad=[]
        for r in rows:
            logs=[(kind,json.loads(details)) for kind,details in db.execute("select eventType,details from logs where runId=? and eventType in ('loot','enter','stage')",(r['id'],))]
            loot={k:sum(e['reward'][k] for kind,e in logs if kind=='loot') for k in ('gold','shards','materials')}
            settlements=db.execute('select * from research_settlement where run_id=?',(r['id'],)).fetchall()
            total=next((x for x in settlements if x['stage_id']==0),None)
            good=total is not None and len(settlements)==5 and loot==dict(zip(('gold','shards','materials'),(total['simulated_gold'],total['simulated_fragments'],total['simulated_materials']))) and all(sum(x[k] for x in settlements if x['stage_id']>0)==total[k] for k in ('simulated_gold','simulated_fragments','simulated_materials','consumable_cost'))
            if r['status']=='COMPLETED':good=good and bool(total['success']) and loot==json.loads(r['rewards']) and {e.get('stage_id') for kind,e in logs if kind in ('enter','stage')}=={1,2,3,4}
            else:good=good and total is not None and not total['success']
            if not good:bad.append(r['id'])
        result=dict(counts=counts,integrity=integrity,broken_frames=missing,reference_hash_errors=refbad,blob_hash_errors=hashbad,foreign_key_errors=fk,decision_reference_errors=decbad,duplicate_settlements=dup,settlement_errors=bad)
        result['passed']=integrity=='ok' and missing==refbad==hashbad==decbad==dup==0 and not fk and not bad and not counts.get('FAILED') and not counts.get('RUNNING')
        return result
def telemetry():
    from scripts.service_common import memory_bytes
    import ctypes
    from ctypes import wintypes
    dll=ctypes.WinDLL('kernel32',use_last_error=True)
    dll.GetCurrentProcess.restype=wintypes.HANDLE
    dll.GetProcessHandleCount.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    handles=wintypes.DWORD()
    okay=dll.GetProcessHandleCount(dll.GetCurrentProcess(),ctypes.byref(handles))
    return {'memory_bytes':memory_bytes(),'handles':handles.value if okay else None,'cpu_seconds':sum(os.times()[:2])}
def freeze(home):
    home.mkdir(parents=True,exist_ok=False)
    files=source_files();git=subprocess.run(['git','status','--porcelain'],cwd=ROOT,capture_output=True,text=True)
    manifest={'utc':stamp(),'source_sha256':source_hash(files),'files':files,'git':git.stdout if git.returncode==0 else 'NOT_AVAILABLE: '+git.stderr.strip(),'python':sys.version,'python_executable':sys.executable,'dependencies':subprocess.run([sys.executable,'-m','pip','freeze'],capture_output=True,text=True,timeout=30).stdout,'node':subprocess.run(['node','--version'],capture_output=True,text=True,timeout=30).stdout,'frontend_dependencies':{n:json.loads((ROOT/'frontend/node_modules'/n/'package.json').read_text())['version'] for n in ('vue','vite','typescript','vue-tsc','@vitejs/plugin-vue')}}
    manifest['installed_python_dependencies']={d.metadata['Name']:d.version for d in importlib.metadata.distributions()}
    with zipfile.ZipFile(home/'frozen-source.zip','x',zipfile.ZIP_DEFLATED) as archive:
        for relative in files:archive.write(ROOT/relative,relative)
    manifest['source_zip_sha256']=digest(home/'frozen-source.zip')
    write(home/'baseline.json',manifest);write(home/'historical-before.json',historical_files())
    print(json.dumps({'source_sha256':manifest['source_sha256'],'root':str(home)}))
def soak(home,seconds):
    baseline=json.loads((home/'baseline.json').read_text())
    assert source_hash(source_files())==baseline['source_sha256'],'Frozen source changed before run'
    assert 1<=seconds<=3600,'This final task never starts 8h/24h'
    run=home/'soak';run.mkdir(exist_ok=False);dbpath=run/'agent.sqlite3'
    configure(home,home/'import.sqlite3');os.environ['MOYU_SOURCE_SHA256']=baseline['source_sha256']
    with sqlite3.connect(f'file:{(ROOT/"reports/v13-baseline/agent.sqlite3").as_posix()}?mode=ro',uri=True) as src,sqlite3.connect(dbpath) as dst:src.backup(dst)
    from backend.main import create_app
    from backend.research_scene import SCENE_ID
    app=create_app(dbpath,tick=.08);r=app.state.runtime
    before=r.store.evidence.status(force=True);started=stamp();began=time.monotonic();wall=time.time();last=began;lastcpu=telemetry()['cpu_seconds'];samples=[];errors=[];pause={};seed=20261013
    with (run/'samples.jsonl').open('w',encoding='utf-8') as out:
        while time.monotonic()-began<seconds:
            if not r.thread or not r.thread.is_alive():
                if r.error:errors.append(r.error);break
                r.start(100,seed=seed,scene_id=SCENE_ID);seed+=100
            elapsed=time.monotonic()-began
            if not pause and elapsed>=min(900,seconds/2):
                r.control('pause');time.sleep(.3);actions=r.world.actions;time.sleep(2)
                held=r.world.actions==actions and r.paused;r.control('resume')
                pause={'utc':stamp(),'elapsed':elapsed,'held_actions':held,'resumed':not r.paused,'pause_seconds':2.3}
                write(run/'pause.json',pause)
            current=time.monotonic();t=telemetry();t['cpu_percent_one_core']=100*(t['cpu_seconds']-lastcpu)/max(.001,current-last)
            with r.store.lock:
                responsive=r.store.db.execute('SELECT 1').fetchone()[0]==1
            t.update(utc=stamp(),elapsed=current-began,wall_elapsed=time.time()-wall,sample_gap=current-last,storage=r.store.evidence.status(force=True),state=r.status(),database_health='RESPONSIVE' if responsive else 'FAIL')
            samples.append(t);out.write(json.dumps(t)+'\n');out.flush();last=current;lastcpu=t['cpu_seconds']
            r.thread.join(min(5,max(.01,seconds-(time.monotonic()-began))))
    stop_requested=stamp()
    if r.thread and r.thread.is_alive():r.control('stop');r.thread.join(30)
    collection=time.monotonic()-began
    if r.error:errors.append(r.error)
    stopped=not(r.thread and r.thread.is_alive())
    r.store.evidence.flush_debug()
    with r.store.lock:r.store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    after=r.store.evidence.status(force=True)
    for repo in (app.state.store,app.state.knowledge,app.state.observation):repo.db.close()
    checks=verify(dbpath);frozen=source_hash(source_files())==baseline['source_sha256']
    history=historical_files();unchanged=history==json.loads((home/'historical-before.json').read_text());write(home/'historical-after-soak.json',{'unchanged':unchanged,'files':history})
    result={'status':'PASS' if collection>=seconds and stopped and not errors and checks['passed'] and frozen and unchanged and pause.get('held_actions') and pause.get('resumed') and max(x['sample_gap'] for x in samples)<30 else 'FAIL','requested_seconds':seconds,'collection_seconds':collection,'wall_seconds':time.time()-wall,'started_utc':started,'ended_utc':stamp(),'stop_requested_utc':stop_requested,'tick':.08,'environment':'SELF_OWNED_SIMULATOR','source_sha256':baseline['source_sha256'],'source_unchanged':frozen,'historical_unchanged':unchanged,'errors':errors,'pause':pause,'safe_stop':stopped,'before':before,'after':after,'verification':checks,'samples':len(samples),'telemetry_ranges':{k:[min(x[k] for x in samples if x[k] is not None),max(x[k] for x in samples if x[k] is not None)] for k in ('memory_bytes','handles','cpu_percent_one_core')},'max_sample_gap':max(x['sample_gap'] for x in samples)}
    write(run/'result.json',result);print(json.dumps(result),flush=True)
    return 0 if result['status']=='PASS' else 1
def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['freeze','soak','verify']);p.add_argument('--root',required=True);p.add_argument('--seconds',type=int,default=3600);a=p.parse_args();home=Path(a.root).resolve()
    if home==ROOT/'reports' or ROOT/'reports' not in home.parents:p.error('dedicated reports child required')
    if a.action=='freeze':freeze(home);return 0
    if a.action=='verify':print(json.dumps(verify(home/'soak/agent.sqlite3')));return 0
    return soak(home,a.seconds)
if __name__=='__main__':sys.exit(main())
