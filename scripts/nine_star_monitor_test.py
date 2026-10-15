import argparse
import json
import time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',required=True);args=parser.parse_args()
    checks=[];errors=[]
    def check(name,passed,details=None):
        checks.append({'name':name,'passed':bool(passed),'details':details})
        assert passed,name
    with httpx.Client(base_url=args.url,timeout=15) as api,sync_playwright() as pw:
        get=lambda path:api.get('/api/'+path).raise_for_status().json()
        response=api.post('/api/agent/start',json={'scene_id':'NineStarResearchDungeon','fault':'path_blocked'},headers={'X-Agent-Control':'local-console'})
        response.raise_for_status();rid=response.json()['runId']
        browser=pw.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1100})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(args.url+'/#/research');page.locator('.service-health summary').click()
        expect(page.locator('.service-health-body')).to_contain_text('最后成功动作')
        expect(page.locator('.service-health-body')).to_contain_text('Worker心跳')
        check('运行监控新增字段实际可见',True)
        until=time.monotonic()+90
        while time.monotonic()<until:
            health=get('service/status');state=health['worker']
            if state['stability']['last_success_at'] and state['stability']['recoveries']:break
            time.sleep(.2)
        check('视觉目标心跳恢复均来自实际动作',bool(state['stability']['observation'] and state['stability']['goal'] and state['stability']['heartbeat_at'] and state['stability']['last_success_at'] and state['stability']['recoveries']),state['stability'])
        check('真实内存CPU进程时间',health['memory_bytes']>0 and health['cpu_percent_one_core']>=0 and health['process_uptime_seconds']>0)
        def first_event(headers=None):
            with api.stream('GET','/api/logs/stream?after=0',headers=headers) as stream:
                stream.raise_for_status()
                for line in stream.iter_lines():
                    if line.startswith('id:'):return int(line.split(':',1)[1])
        first=first_event();expected=get('logs?after='+str(first)+'&limit=1')[0]['id']
        resumed=first_event({'Last-Event-ID':str(first)})
        check('SSE断开并按真实游标补读',resumed==expected,{'before':first,'after':resumed})
        while time.monotonic()<until:
            state=get('agent/status')
            if not state['running']:break
            time.sleep(.2)
        check('SSE断线不影响真实通关',state['runId']==rid and not state['running'] and state['completedSession']==1,state)
        rows=get('logs?runId='+rid+'&limit=2000')
        exported=[json.loads(line) for line in api.get('/api/logs/export?runId='+rid).raise_for_status().text.splitlines()]
        check('分页流式导出与实际日志一致',rows==exported)
        time.sleep(2);page.screenshot(path=str(ROOT/'reports/9star-monitor.png'),full_page=True)
        check('新增监控没有浏览器JS异常',not errors,errors)
        browser.close()
    (ROOT/'reports/9star-monitor.json').write_text(json.dumps({'passed':True,'checks':checks,'browser_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'checks':len(checks)}))

if __name__=='__main__':main()
