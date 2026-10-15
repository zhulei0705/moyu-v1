import argparse,json,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import httpx
from playwright.sync_api import sync_playwright,expect
from scripts.service_common import directory,read,port_open

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8012);a=parser.parse_args();url=f'http://127.0.0.1:{a.port}'
    checks=[];errors=[]
    def check(name,result,detail=None):checks.append({'name':name,'passed':bool(result),'details':detail});assert result,name
    with httpx.Client(base_url=url,timeout=10) as api,sync_playwright() as pw:
        before=api.get('/api/runs').json();earnings=api.get('/api/research/earnings').json()
        manager=read(directory(a.port)/'state.json');assert manager['watchdog_enabled']
        check('压力测试服务实例身份匹配',api.get('/api/health').json()['instance_id']==manager['instance_id'])
        browser=pw.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
        try:
            page.goto(url+'/#/research');expect(page.locator('.service-health summary')).to_contain_text('RUNNING',timeout=10000)
            page.locator('.service-health summary').click();expect(page.locator('.service-health-body')).to_contain_text('STOP_UNCONFIRMED_NO_REPLAY')
            check('Web显示真实心跳启动时间Worker与恢复策略',bool(api.get('/api/service/status').json()['heartbeat_at']))
            subprocess.run(['taskkill','/PID',str(manager['server_pid']),'/F'],capture_output=True,check=True)
            expect(page.locator('.badge')).to_contain_text('API 未连接',timeout=12000)
            check('真实服务异常退出后Web明确断线',True)
            expect(page.locator('.badge')).to_contain_text('API 已连接',timeout=25000)
            expect(page.locator('.service-health summary')).to_contain_text('Worker IDLE',timeout=15000)
            state=api.get('/api/service/status').json()
            check('Watchdog恢复后新实例和实际重启次数',state['instance_id']!=manager['instance_id'] and state['restart_count']==manager['restart_count']+1,state)
            check('Web显示真实异常检测时间',state['last_unexpected_exit_detected_at'] is not None)
            check('完成记录与结算重启后完全一致',api.get('/api/runs').json()==before and api.get('/api/research/earnings').json()==earnings)
            page.screenshot(path=str(ROOT/'reports/v132-reliability.png'),full_page=True)
            stopped=subprocess.run([sys.executable,str(ROOT/'scripts/service_manager.py'),'stop','--port',str(a.port)],capture_output=True,timeout=30)
            expect(page.locator('.badge')).to_contain_text('API 未连接',timeout=10000);time.sleep(3)
            check('主动停止后Web断线且Watchdog不拉起',stopped.returncode==0 and not port_open(a.port) and read(directory(a.port)/'state.json')['state']=='STOPPED')
            check('浏览器无未处理JavaScript异常',not errors,errors)
        finally:
            browser.close()
            (ROOT/'reports/v132-reliability-browser.json').write_text(json.dumps({'checks':checks,'errors':errors,'passed':all(c['passed'] for c in checks)},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'checks':len(checks)}))
if __name__=='__main__':main()
