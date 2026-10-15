"""V1.3.1 console failures use network interception; real actions use isolated simulator DB."""
import argparse,json,time,os,sys,subprocess,sqlite3,uuid
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT))
from scripts.test_artifacts import report_root, require_test_service
REPORT=report_root()
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8005');url=parser.parse_args().url
    require_test_service(url)
    checks=[];errors=[];network=[]
    def check(name,condition,detail=None):
        checks.append({'name':name,'passed':bool(condition),'detail':detail});assert condition,name
    with httpx.Client(base_url=url,timeout=15) as api,sync_playwright() as pw:
        def get(path):
            r=api.get('/api/'+path);r.raise_for_status();return r.json()
        def post(action,body=None):
            r=api.post('/api/agent/'+action,json=body,headers={'X-Agent-Control':'local-console'});r.raise_for_status();return r.json()
        browser=pw.chromium.launch();context=browser.new_context(viewport={'width':1440,'height':1100});page=context.new_page()
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('requestfailed',lambda r:network.append({'url':r.url,'failure':r.failure}))
        try:
            check('健康API真实返回当前稳定性版本',get('health')['version'] in ('1.3.1','1.3.2','1.3.3'))
            page.goto(url+'/#/research')
            start=page.get_by_role('button',name='启动 Agent',exact=False)
            expect(start).to_be_enabled();expect(page.locator('.badge')).to_contain_text('API 已连接')
            check('正常连接与真实Worker闲置',not get('agent/status')['running'])
            check('记录最后成功连接时间','待获取' not in page.locator('.connection-time').inner_text())
            check('未加载九星背包显示待获取且无NaN','待获取 / 待获取' in page.locator('.controls').inner_text() and 'NaN' not in page.locator('body').inner_text())
            # First-load failure must not prevent timers or bootstrap recovery.
            page.route('**/api/**',lambda route:route.abort('connectionrefused'))
            page.reload();expect(page.get_by_role('alert')).to_contain_text('无法连接本地服务');expect(start).to_be_disabled()
            check('首次加载后端不可达中文提示且禁用控制',True)
            check('没有伪造IDLE或零值',page.locator('.state').inner_text()=='待获取' and 'TypeError' not in page.locator('.controls').inner_text())
            page.unroute('**/api/**');expect(start).to_be_enabled(timeout=10000)
            check('首次失败后无需刷新即可自动恢复',True)
            # Status request exceeds actual AbortController budget.
            def delayed(route):
                time.sleep(4.5) # Browser AbortController expires independently of the test driver.
                try:route.abort('timedout')
                except Exception:pass
            page.route('**/api/agent/status',delayed)
            expect(page.get_by_role('alert')).to_contain_text('请求超时',timeout=10000)
            check('实际超时中止并显示中文状态未知',start.is_disabled())
            page.unroute('**/api/agent/status');expect(start).to_be_enabled(timeout=12000)
            check('超时后轮询恢复',True)
            # 500 is a failed command, never an assumed running worker.
            page.route('**/api/agent/start',lambda route:route.fulfill(status=500,json={'detail':'测试启动服务失败'}))
            start.click();expect(page.get_by_role('alert')).to_contain_text('测试启动服务失败')
            check('启动HTTP失败真实展示且Worker未运行',not get('agent/status')['running'])
            page.unroute('**/api/agent/start');page.get_by_role('button',name='重新同步 / 重试连接').click()
            page.route('**/api/agent/start',lambda route:route.fulfill(json={'runId':'unverified','running':True}))
            start.click();expect(page.get_by_role('alert')).to_contain_text('Worker 未达到预期状态')
            check('HTTP200不能冒充启动成功',not get('agent/status')['running'])
            page.unroute('**/api/agent/start');page.get_by_role('button',name='重新同步 / 重试连接').click()
            # Invalid and empty metric fields, including history and decision latency.
            page.route('**/api/dungeon/status',lambda route:route.fulfill(json={'loaded':True,'scene_id':'NineStarResearchDungeon','stage':None,'hp':'bad','attack':None,'bossRemaining':'bad','actions':None,'bag_capacity':None,'bag_used':None}))
            page.route('**/api/inventory',lambda route:route.fulfill(json={'gold':'invalid','shards':None,'materials':False}))
            page.route('**/api/research/earnings',lambda route:route.fulfill(json={'completed':1,'failed':None,'simulated_gold':'bad','items':[{'run_id':'invalid-metric','success':True,'duration':'bad'}]}))
            page.wait_for_timeout(1600)
            text=page.locator('body').inner_text()
            check('空字段和非数字不会产生NaNInfinity或渲染异常','NaN' not in text and 'Infinity' not in text and '暂无有效数据' in text and '待获取 / 待获取' in text)
            page.unroute('**/api/dungeon/status')
            page.route('**/api/dungeon/status',lambda route:route.fulfill(json={'loaded':True,'scene_id':'NineStarResearchDungeon','stage':0,'hp':0,'attack':0,'bossRemaining':0,'actions':0,'bag_capacity':0,'bag_used':0,'consumable_cost':0}))
            page.wait_for_timeout(1000)
            check('确认容量和占用均为零时正确显示0 / 0','模拟背包空位 / 容量\n0 / 0' in page.locator('.controls').inner_text())
            for pattern in ['**/api/dungeon/status','**/api/inventory','**/api/research/earnings']:page.unroute(pattern)
            # Real worker and duplicate click/API rejection.
            page.locator('.scene-select select').select_option('original');page.locator('.settings input').first.fill('2')
            posts=[]
            page.on('request',lambda r:posts.append(r.url) if r.method=='POST' and r.url.endswith('/api/agent/start') else None)
            start.evaluate('(b)=>{b.click();b.click()}')
            pause=page.get_by_role('button',name='暂停',exact=True);expect(pause).to_be_enabled(timeout=10000)
            state=get('agent/status');run=state['runId']
            check('双击仅提交一次且查询确认真实Worker',len(posts)==1 and state['running'],{'run_id':run})
            check('后端重复启动409且原任务不变',api.post('/api/agent/start',json={},headers={'X-Agent-Control':'local-console'}).status_code==409 and get('agent/status')['runId']==run)
            pause.click();expect(page.get_by_role('button',name='继续',exact=True)).to_be_enabled()
            page.reload();expect(page.get_by_role('button',name='继续',exact=True)).to_be_enabled(timeout=10000)
            check('浏览器刷新恢复真实暂停状态',get('agent/status')['paused'] and get('agent/status')['runId']==run)
            # Offline UI cannot stop/change actual task. Native EventSource must reconnect with Last-Event-ID.
            page.wait_for_timeout(800)
            ids_before=[int(s.split('/')[0].strip().lstrip('#')) for s in page.locator('.log-line details summary').all_text_contents()]
            cursor=max(ids_before,default=0);reconnect_headers=[]
            page.on('request',lambda r:reconnect_headers.append(r.headers.get('last-event-id')) if '/api/logs/stream' in r.url else None)
            context.set_offline(True);page.wait_for_timeout(1800)
            check('断网禁用控制但不改变后端任务',not page.get_by_role('button',name='继续',exact=True).is_enabled() and get('agent/status')['paused'])
            check('断网保留明确最后已知状态','最后已知状态' in page.locator('.connection-time').inner_text())
            post('resume');time.sleep(.5);post('pause')
            context.set_offline(False)
            expect(page.get_by_role('button',name='继续',exact=True)).to_be_enabled(timeout=12000)
            expect(page.locator('.badge')).to_contain_text('SSE 实时连接',timeout=15000)
            page.wait_for_timeout(1000)
            ids_after=[int(s.split('/')[0].strip().lstrip('#')) for s in page.locator('.log-line details summary').all_text_contents()]
            check('SSE中断后自动补读新日志且去重',max(ids_after)>cursor and len(ids_after)==len(set(ids_after)),{'cursor':cursor,'latest':max(ids_after),'reconnect_headers':reconnect_headers})
            page.get_by_role('button',name='停止',exact=True).click();expect(start).to_be_enabled(timeout=10000)
            check('停止后再次查询真实Worker结束',not get('agent/status')['running'])
            # Many real historical rows from simulator runs: no fabricated event injection.
            post('start',{'rounds':3,'seed':41})
            deadline=time.monotonic()+80
            while get('agent/status')['running'] and time.monotonic()<deadline:time.sleep(.2)
            check('三轮实际运行产生大量可补读历史',not get('agent/status')['running'] and len(get('logs?limit=2000'))>=1000)
            page.reload();expect(start).to_be_enabled(timeout=15000);page.wait_for_timeout(1000)
            check('历史日志显示上限500且控制可交互',page.locator('.log-line').count()<=500 and start.is_enabled(),{'visible_logs':page.locator('.log-line').count()})
            oldest=get('logs?limit=1');latest=get('logs?limit=1&tail=true')[0]['id']
            page.route('**/api/logs?limit=500&tail=true',lambda route:route.fulfill(json=oldest))
            page.reload();expect(start).to_be_enabled(timeout=15000)
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                visible=[int(s.split('/')[0].strip().lstrip('#')) for s in page.locator('.log-line details summary').all_text_contents()]
                if visible and max(visible)>=latest:break
                page.wait_for_timeout(200)
            check('SSE从真实旧游标补读数千日志仍保持500行和交互',visible and max(visible)>=latest and len(visible)<=500 and len(visible)==len(set(visible)) and start.is_enabled(),{'after':oldest[0]['id'],'latest':latest,'visible':len(visible)})
            page.unroute('**/api/logs?limit=500&tail=true')
            # Reconnect server process really exits and starts against same isolated SQLite.
            check_real_restart(pw,checks,errors)
            check('浏览器无未处理JS异常',not errors,errors)
            page.goto(url+'/#/research');expect(start).to_be_enabled(timeout=15000);page.screenshot(path=str(REPORT/'v131-console.png'),full_page=True)
        finally:
            context.set_offline(False)
            if get('agent/status')['running']:post('stop')
            browser.close()
            (REPORT/'v131-browser-results.json').write_text(json.dumps({'passed':all(c['passed'] for c in checks),'checks':checks,'browser_errors':errors,'network_failures':network},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'checks':len(checks)},ensure_ascii=False))

def check_real_restart(pw,checks,errors):
    path=REPORT/('v131-restart-'+str(uuid.uuid4())+'.sqlite3')
    # Independent consistent snapshot; no dependency on a prior report directory.
    from backend.db_isolation import database_path
    source_path=database_path()
    if source_path.is_file():
        with sqlite3.connect(f'file:{source_path.as_posix()}?mode=ro',uri=True) as source,sqlite3.connect(path) as target:source.backup(target)
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    env=dict(os.environ,MOYU_DB=str(path))
    log=(REPORT/'v131-restart-server.log').open('w',encoding='utf-8')
    def launch():
        process=subprocess.Popen([sys.executable,'-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log)
        for _ in range(80):
            try:
                if httpx.get(url+'/api/health',timeout=.5).status_code==200:return process
            except httpx.HTTPError:pass
            time.sleep(.2)
        process.terminate();raise TimeoutError('restart test service')
    process=launch();browser=pw.chromium.launch();page=browser.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
    try:
        page.goto(url);start=page.get_by_role('button',name='启动 Agent',exact=False);expect(start).to_be_enabled(timeout=15000)
        original=httpx.get(url+'/api/runs').json()
        process.terminate();process.wait(timeout=15)
        expect(page.locator('.badge')).to_contain_text('API 未连接',timeout=15000);expect(start).to_be_disabled()
        process=launch();expect(start).to_be_enabled(timeout=15000)
        assert httpx.get(url+'/api/runs').json()==original
        checks.append({'name':'真实服务重启后自动恢复连接并保留历史','passed':True})
    finally:
        browser.close();process.terminate();process.wait(timeout=15);log.close()

if __name__=='__main__':main()
