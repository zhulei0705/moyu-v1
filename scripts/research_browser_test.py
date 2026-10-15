import argparse,json,time
from pathlib import Path
from datetime import datetime,timezone
import httpx
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT))
from scripts.test_artifacts import report_root, require_test_service
REPORT=report_root()
SCENE='NineStarResearchDungeon'
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8003');url=parser.parse_args().url
    require_test_service(url)
    checks=[];errors=[];run_ids=[]
    def check(name,ok,details=None):
        checks.append({'name':name,'passed':bool(ok),'details':details});assert ok,name
    try:
        with httpx.Client(base_url=url,timeout=30) as api,sync_playwright() as pw:
            def get(path):
                r=api.get('/api/'+path);r.raise_for_status();return r.json()
            def finished(timeout=100):
                until=time.monotonic()+timeout
                while time.monotonic()<until:
                    s=get('agent/status')
                    if not s['running']:return s
                    time.sleep(.2)
                raise TimeoutError('研究Worker运行未结束')
            baseline=get('research/earnings')['completed']
            before_ids={r['run_id'] for r in get('research/earnings')['items']}
            browser=pw.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(url+'/#/research');expect(page.get_by_role('heading',name='九星副本运行',exact=True)).to_be_visible()
            check('九星运行页面中文且独立加载',True)
            selector=page.locator('.scene-select select');expect(selector).to_have_value(SCENE);check('场景选择器保留原创与九星场景',selector.locator('option').count()==2)
            page.locator('.settings input').first.fill('2');page.get_by_role('button',name='启动 Agent',exact=False).click()
            expect(page.get_by_role('button',name='暂停',exact=True)).to_be_enabled()
            state=get('agent/status');run_id=state['runId'];check('点击启动实际研究Worker',state['running'] and state['scene_id']==SCENE)
            frame1=api.get('/api/frame/latest').content;time.sleep(.6);frame2=api.get('/api/frame/latest').content;check('实时PNG画面随实际动作变化',frame1!=frame2 and frame2.startswith(b'\x89PNG'))
            page.get_by_role('button',name='暂停',exact=True).click();expect(page.get_by_role('button',name='继续',exact=True)).to_be_enabled();actions=get('dungeon/status')['actions'];time.sleep(.5)
            check('九星暂停屏障阻止动作',get('dungeon/status')['actions']==actions)
            page.reload();expect(page.get_by_role('button',name='继续',exact=True)).to_be_enabled();check('刷新恢复场景和暂停运行ID',get('agent/status')['runId']==run_id and selector.input_value()==SCENE)
            page.get_by_role('button',name='继续',exact=True).click();time.sleep(.6)
            check('恢复后真实动作推进',get('dungeon/status')['actions']>actions)
            expect(page.locator('.decision-list article').first).to_be_visible();check('页面显示真实决策原因',bool(page.locator('.decision-list article p').first.inner_text()))
            check('地图路线与角色位置显示',page.locator('.route-frame svg circle').count()==1)
            page.screenshot(path=str(REPORT/'v13-running.png'),full_page=True)
            state=finished();check('连续实际完成两轮九星四阶段',state['completedSession']==2 and state['error'] is None,state)
            earnings=get('research/earnings');check('独立收益报表增加两条完成记录',earnings['completed']==baseline+2)
            current=[r for r in earnings['items'] if r['run_id'] not in before_ids];run_ids=[r['run_id'] for r in current]
            for run in current:
                ds=get('research/decisions?run_id='+run['run_id']+'&limit=1000')['items']
                check('四阶段决策确有图像与A* '+run['run_id'][:8],{d['stage_id'] for d in ds}=={0,1,2,3,4} and any(d['route'] for d in ds))
                required=['run_id','stage_id','observation_id','world_state','selected_goal','action_type','decision_reason','expected_result','actual_result','decision_latency','error_code']
                check('决策解释字段完整 '+run['run_id'][:8],all(all(k in d for k in required) for d in ds))
                oid=ds[0]['observation_id'];check('决策可读取保存的原始图像 '+run['run_id'][:8],api.get('/api/research/observations/'+oid+'/frame').content.startswith(b'\x89PNG'))
                logs=get('runs/'+run['run_id'])['events'];settled=get('research/settlements?run_id='+run['run_id'])['items']
                total=next(r for r in settled if r['stage_id']==0)
                check('四阶段与全程资源结算守恒 '+run['run_id'][:8],len(settled)==5 and sum(r['simulated_gold'] for r in settled if r['stage_id']>0)==total['simulated_gold']==sum(e['details']['reward']['gold'] for e in logs if e['eventType']=='loot'))
                check('通关由战斗拾取和实际退出验证 '+run['run_id'][:8],all(any(e['eventType']==kind for e in logs) for kind in ['enter','attack','defeat','loot','complete','exit','RESEARCH_SETTLEMENT']))
                context=get('research/runs/'+run['run_id']);check('运行引用V1.2孪生版本与模拟假设 '+run['run_id'][:8],bool(context['twin_revision_id']) and context['configuration']['configuration']['map_regions'][0]['data_class']=='SIMULATED_ASSUMPTION')
            expect(page.get_by_role('button',name='启动 Agent',exact=False)).to_be_enabled()
            page.get_by_role('button',name='查看 / 回放').first.click();expect(page.locator('.decision-replay img')).to_be_visible();check('历史回放加载真实决策PNG',True)
            page.get_by_role('button',name='关闭',exact=True).click();page.screenshot(path=str(REPORT/'v13-complete.png'),full_page=True)
            for command,label in [('stop','停止'),('emergency-stop','紧急停止')]:
                page.locator('.settings input').first.fill('1');page.get_by_role('button',name='启动 Agent',exact=False).click();expect(page.get_by_role('button',name=label,exact=True)).to_be_enabled();page.get_by_role('button',name=label,exact=True).click();state=finished()
                check('九星 '+label+' 正常释放Worker',get('runs/'+state['runId'])['status']=='STOPPED')
                expect(page.get_by_role('button',name='启动 Agent',exact=False)).to_be_enabled()
            all_logs=[];cursor=0
            while True:
                rows=get('logs?after='+str(cursor)+'&limit=2000')
                if not rows:break
                all_logs+=rows;cursor=rows[-1]['id']
            with api.stream('GET','/api/logs/stream',headers={'Last-Event-ID':str(all_logs[-4]['id'])}) as response:
                received=[]
                for line in response.iter_lines():
                    if line.startswith('data: '):
                        received.append(json.loads(line[6:]))
                        if len(received)==3:break
            check('九星SSE断线历史补读一致',received==all_logs[-3:])
            check('收益单位明确且不换算人民币',earnings['source']=='SIMULATOR' and earnings['currency_conversion'] is None)
            check('浏览器无JavaScript异常',not errors,errors);browser.close()
    except Exception as exc:
        checks.append({'name':'执行异常','passed':False,'details':repr(exc)})
        raise
    finally:
        result={'timestamp':datetime.now(timezone.utc).isoformat(),'url':url,'checks':checks,'passed':all(c['passed'] for c in checks),'browserErrors':errors,'completed_run_ids':run_ids}
        (REPORT/'v13-browser-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
