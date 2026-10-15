"""Actual monotonic-clock simulator soak. Never uses accelerated time."""
import argparse
import os
import sys
import time
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=3600)
    parser.add_argument('--label', default='v133-1h')
    parser.add_argument('--allow-long-run', action='store_true')
    parser.add_argument('--approval-note', default='')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 86400:
        parser.error('测试时长必须为1秒至24小时')
    if args.seconds > 3600 and (not args.allow_long_run or len(args.approval_note.strip()) < 10):
        parser.error('8/24小时必须先获得用户明确批准，并显式提供批准记录')
    home = ROOT/'reports'/args.label
    home.mkdir(exist_ok=False)
    path = home/'agent.sqlite3'
    # Read an explicit per-run policy prepared before starting the directory.
    policy_source = ROOT/'reports'/(args.label+'.retention.json')
    if policy_source.exists():
        path.with_suffix(path.suffix+'.retention.json').write_bytes(policy_source.read_bytes())
    with sqlite3.connect(ROOT/'reports/v13-baseline/agent.sqlite3') as source, sqlite3.connect(path) as target:
        source.backup(target)
    os.environ['MOYU_DB'] = str(home/'import.sqlite3')
    from backend.main import create_app
    from backend.research_scene import SCENE_ID
    from scripts.service_common import memory_bytes
    app = create_app(path, tick=.08)
    runtime = app.state.runtime
    began = time.monotonic()
    started = datetime.now(timezone.utc).isoformat()
    samples = []
    failures = []
    seed = 20261012
    with (home/'samples.jsonl').open('w', encoding='utf-8') as output:
        while time.monotonic()-began < args.seconds:
            if not runtime.thread or not runtime.thread.is_alive():
                if runtime.error:
                    failures.append(runtime.error)
                    break
                runtime.start(100, seed=seed, scene_id=SCENE_ID)
                seed += 100
            sample = dict(elapsed=time.monotonic()-began, memory_bytes=memory_bytes(),
                storage=runtime.store.evidence.status(force=True),
                completed=runtime.store.query("SELECT COUNT(*) n FROM runs r JOIN research_run x ON x.run_id=r.id WHERE r.status='COMPLETED'")[0]['n'])
            samples.append(sample)
            output.write(json.dumps(sample)+'\n')
            output.flush()
            runtime.thread.join(min(5, max(.01, args.seconds-(time.monotonic()-began))))
    if runtime.thread and runtime.thread.is_alive():
        runtime.control('stop')
        runtime.thread.join(20)
    collection_seconds=time.monotonic()-began
    if runtime.error and runtime.error not in failures:
        failures.append(runtime.error)
    db = runtime.store.db
    with runtime.store.lock:
        integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
        missing = db.execute('''SELECT count(*) FROM agent_observation o LEFT JOIN evidence_frame_ref r
            ON r.observation_id=o.id LEFT JOIN evidence_blob b ON b.hash=r.hash
            WHERE length(o.frame_png)=0 AND b.hash IS NULL''').fetchone()[0]
        complete = db.execute("SELECT count(*) FROM runs r JOIN research_run x ON x.run_id=r.id WHERE r.status='COMPLETED'").fetchone()[0]
        duplicate = db.execute('SELECT count(*) FROM (SELECT run_id,stage_id,count(*) n FROM research_settlement GROUP BY run_id,stage_id HAVING n>1)').fetchone()[0]
        unsettled = db.execute("SELECT count(*) FROM runs r JOIN research_run x ON x.run_id=r.id WHERE status='COMPLETED' AND NOT EXISTS(SELECT 1 FROM research_settlement s WHERE s.run_id=r.id AND s.stage_id=0 AND success=1)").fetchone()[0]
        blobs = db.execute('SELECT count(*),sum(length(png)) FROM evidence_blob').fetchone()
        observations = db.execute('SELECT count(*) FROM agent_observation').fetchone()[0]
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    result = dict(started_at_utc=started, ended_at_utc=datetime.now(timezone.utc).isoformat(),
        approval_note=args.approval_note if args.seconds>3600 else 'User authorized V1.3.3 one-hour simulator test',
        requested_seconds=args.seconds, collection_seconds=collection_seconds, actual_seconds=time.monotonic()-began, tick=.08,
        environment='SELF_OWNED_SIMULATOR', completed=complete, failures=failures,
        integrity=integrity, broken_frame_references=missing, duplicate_settlements=duplicate,
        completed_without_settlement=unsettled, unique_frames=blobs[0], frame_bytes=blobs[1],
        observations=observations, final_storage=runtime.store.evidence.status(force=True),
        passed=collection_seconds >= args.seconds and not failures and integrity=='ok' and missing==duplicate==unsettled==0)
    (home/'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
    for repository in (runtime.store, app.state.knowledge, app.state.observation):
        repository.db.close()
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
