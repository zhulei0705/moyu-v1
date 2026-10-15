"""真实浏览器与运行服务验收，始终保存结果；不插入伪造任务。"""
import argparse
import json
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
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    args=parser.parse_args()
    require_test_service(args.url)
    REPORT.mkdir(exist_ok=True)
    checks=[]
    browser_errors=[]
    def check(name, condition, details=None):
        checks.append({'name':name,'passed':bool(condition),'details':details})
        assert condition, name
    with httpx.Client(base_url=args.url,timeout=10) as api:
        def get(path):
            r=api.get('/api/'+path);r.raise_for_status();return r.json()
        def wait_stopped(timeout=80):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                s=get('agent/status')
                if not s['running']: return s
                time.sleep(0.2)
            raise TimeoutError('实际 Worker 未结束')
        try:
            check('后端健康',get('health')['status']=='ok')
            baseline=get('earnings')['completed']
            with sync_playwright() as p:
                browser=p.chromium.launch()
                page=browser.new_page(viewport={'width':1440,'height':1100})
                page.on('pageerror',lambda e:browser_errors.append(str(e)))
                page.goto(args.url)
                expect(page.get_by_role('heading',name='游戏 Agent 控制台')).to_be_visible()
                check('浏览器打开实际 Vue 页面',True)
                page.get_by_role('button',name='启动 Agent').click()
                expect(page.get_by_role('button',name='暂停',exact=True)).to_be_enabled()
                time.sleep(0.8)
                s=get('agent/status');run_id=s['runId']
                check('页面启动创建运行实例',s['running'] and bool(run_id),{'runId':run_id})
                check('重复启动被拒绝',api.post('/api/agent/start',json={},headers={'X-Agent-Control':'local-console'}).status_code==409)
                page.get_by_role('button',name='暂停',exact=True).click()
                deadline=time.monotonic()+3
                while not get('agent/status')['paused'] and time.monotonic()<deadline: time.sleep(0.05)
                paused_actions=get('dungeon/status')['actions']
                time.sleep(0.7)
                check('暂停屏障阻止真实动作',get('dungeon/status')['actions']==paused_actions)
                page.reload()
                expect(page.get_by_role('button',name='继续',exact=True)).to_be_enabled()
                check('刷新恢复实际暂停状态',get('agent/status')['runId']==run_id)
                page.get_by_role('button',name='继续',exact=True).click()
                time.sleep(0.8)
                check('恢复继续推进',get('dungeon/status')['actions']>paused_actions)
                page.screenshot(path=str(REPORT/'console-running.png'),full_page=True)
                check('浏览器实时收到真实 SSE 日志',page.locator('.log-line').count()>5)
                state=wait_stopped()
                check('连续完成两轮真实副本',state['completedSession']==2 and not state['error'],state)
                check('模拟收益真实累计',get('earnings')['completed']==baseline+2 and get('earnings')['materials']>=16)
                page.reload()
                expect(page.locator('tbody tr').first).to_be_visible()
                check('刷新后恢复任务历史',page.locator('tbody tr').count()>=2)
                page.get_by_role('button',name='查看 / 回放').first.click()
                expect(page.get_by_role('button',name='播放事件')).to_be_visible()
                check('历史事件可回放',page.locator('.replay pre').inner_text().find('eventType')>=0)
                page.get_by_role('button',name='关闭',exact=True).click()
                page.locator('.settings input').first.fill('1')
                page.locator('.fault select').select_option('stuck')
                previous_id=get('agent/status')['runId']
                page.get_by_role('button',name='启动 Agent').click()
                deadline=time.monotonic()+10
                while get('agent/status')['runId']==previous_id and time.monotonic()<deadline:
                    time.sleep(.1)
                check('阻塞测试启动新任务已确认',get('agent/status')['runId']!=previous_id)
                recovered=wait_stopped()
                recover_id=recovered['runId']
                check('移动阻塞注入恢复后通关',recovered['completedSession']==1 and not recovered['error'])
                check('恢复事件可观察',any(e['eventType']=='recover' for e in get('runs/'+recover_id)['events']))
                # 确保前端下一次轮询已解除 disabled。
                expect(page.get_by_role('button',name='启动 Agent')).to_be_enabled()
                page.locator('.fault select').select_option('crash')
                page.get_by_role('button',name='启动 Agent').click()
                time.sleep(0.5)
                failed=wait_stopped()
                check('Worker 故障明确失败',get('runs/'+failed['runId'])['status']=='FAILED' and bool(failed['error']))
                expect(page.get_by_role('button',name='启动 Agent')).to_be_enabled()
                page.locator('.fault select').select_option('')
                page.get_by_role('button',name='启动 Agent').click()
                expect(page.get_by_role('button',name='停止',exact=True)).to_be_enabled()
                page.get_by_role('button',name='停止',exact=True).click()
                stopped=wait_stopped()
                check('停止释放 Worker',get('runs/'+stopped['runId'])['status']=='STOPPED')
                expect(page.get_by_role('button',name='启动 Agent')).to_be_enabled()
                page.get_by_role('button',name='启动 Agent').click()
                expect(page.get_by_role('button',name='紧急停止',exact=True)).to_be_enabled()
                page.get_by_role('button',name='紧急停止',exact=True).click()
                emergency=wait_stopped()
                check('紧急停止释放 Worker',get('runs/'+emergency['runId'])['status']=='STOPPED')
                page.reload()
                page.screenshot(path=str(REPORT/'console-final.png'),full_page=True)
                check('浏览器无 JavaScript 异常',not browser_errors,browser_errors)
                browser.close()
            exported=api.get('/api/logs/export').text
            (REPORT/'actual-events.jsonl').write_text(exported,encoding='utf-8')
            rows=[json.loads(line) for line in exported.splitlines()]
            cursor=0; paged=[]
            while True:
                batch=get(f'logs?after={cursor}&limit=2000')
                if not batch: break
                paged.extend(batch);cursor=batch[-1]['id']
            check('日志查询与数据库 JSONL 一致',paged==rows,{'events':len(rows)})
            with api.stream('GET','/api/logs/stream',headers={'Last-Event-ID':str(rows[-4]['id'])}) as response:
                received=[]
                for line in response.iter_lines():
                    if line.startswith('data: '):
                        received.append(json.loads(line[6:]))
                        if len(received)==3: break
            check('SSE Last-Event-ID 断线补读一致',received==rows[-3:])
            check('资源由实际拾取与结算事件产生',all(sum(e['details']['reward']['gold'] for e in get('runs/'+r['id'])['events'] if e['eventType']=='loot')==r['rewards']['gold'] for r in get('runs') if r['status']=='COMPLETED'))
        except Exception as exc:
            checks.append({'name':'执行异常','passed':False,'details':repr(exc)})
        finally:
            result={'timestamp':datetime.now(timezone.utc).isoformat(),'url':args.url,'source':'SIMULATED','checks':checks,'passed':all(c['passed'] for c in checks),'browserErrors':browser_errors}
            (REPORT/'browser-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['passed'] else 1

if __name__=='__main__': raise SystemExit(main())
