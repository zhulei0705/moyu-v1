"""Test-only high-volume evidence, HTTP, UI refresh and SSE catch-up."""
import argparse
import json
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timezone
import httpx
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT))
from scripts.test_artifacts import report_root, require_test_service
REPORT=report_root()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--db', required=True)
    args=parser.parse_args()
    require_test_service(args.url)
    from backend.db_isolation import validate_path
    path=validate_path(args.db)
    live=require_test_service(args.url)
    if path!=Path(live['database_path']).resolve():parser.error('test service/database mismatch')
    from backend.db_isolation import test_root
    if test_root() not in path.parents:
        parser.error('压力写入仅允许reports下隔离数据库')
    result={'checks':[]}
    def check(name,passed,details=None):
        result['checks'].append(dict(name=name,passed=bool(passed),details=details))
        assert passed,name
    with sqlite3.connect(path,timeout=20) as db:
        start=db.execute('SELECT COALESCE(MAX(id),0) FROM logs').fetchone()[0]
        payload=json.dumps({'source':'SIMULATOR_TEST','padding':'x'*1024})
        began=time.monotonic()
        db.executemany('INSERT INTO logs(timestamp,sequence,runId,stage,level,eventType,message,details) VALUES(?,?,?,?,?,?,?,?)',
            [(datetime.now(timezone.utc).isoformat(),i,None,'TEST','DEBUG','STORAGE_PRESSURE','isolated debug stress',payload) for i in range(1,5001)])
        db.commit()
        write_seconds=time.monotonic()-began
    with httpx.Client(base_url=args.url,timeout=30) as api:
        latencies=[]
        for _ in range(30):
            began=time.monotonic()
            response=api.get('/api/agent/status');response.raise_for_status()
            latencies.append(time.monotonic()-began)
        check('30 status reads responsive',max(latencies)<2,dict(max_seconds=max(latencies),mean_seconds=sum(latencies)/len(latencies)))
        seen=[];began=time.monotonic()
        with api.stream('GET','/api/logs/stream?after='+str(start)) as stream:
            stream.raise_for_status()
            for line in stream.iter_lines():
                if line.startswith('data: '):
                    seen.append(json.loads(line[6:])['id'])
                    if len(seen)==5000:break
        check('5000 SSE history events ordered no gaps',seen==list(range(start+1,start+5001)),dict(seconds=time.monotonic()-began))
        with api.stream('GET','/api/logs/stream',headers={'Last-Event-ID':str(start+4999)}) as stream:
            for line in stream.iter_lines():
                if line.startswith('id: '):
                    check('SSE cursor reconnect resumes exactly',int(line[4:])==start+5000)
                    break
        with sync_playwright() as pw:
            browser=pw.chromium.launch()
            page=browser.new_page(viewport={'width':1440,'height':1100})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            for _ in range(4):
                page.goto(args.url+'/#/research')
                expect(page.locator('.storage-health')).to_contain_text('长期运行容量',timeout=10000)
                expect(page.locator('.badge')).to_contain_text('API 已连接',timeout=10000)
                page.wait_for_timeout(500)
            check('four UI refreshes with storage and API connected',not errors,errors)
            page.screenshot(path=str(REPORT/'v133-storage-pressure.png'),full_page=True)
            browser.close()
    result.update(passed=True,write_seconds=write_seconds,inserted_debug_rows=5000)
    (REPORT/'v133-storage-pressure.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
