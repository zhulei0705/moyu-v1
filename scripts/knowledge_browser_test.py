import argparse
import json
import time
from pathlib import Path
from datetime import datetime,timezone
import httpx
from playwright.sync_api import sync_playwright,expect

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
    checks=[];errors=[]
    def check(name,condition,details=None):
        checks.append({'name':name,'passed':bool(condition),'details':details})
        assert condition,name
    try:
        with httpx.Client(base_url=args.url,timeout=15) as api:
            paths=['dungeons','dungeons/alama','dungeons/alama/stages','dungeons/alama/bosses','dungeons/alama/rewards','sources','conflicts']
            for path in paths:
                response=api.get('/api/knowledge/'+path)
                check('只读 API '+path,response.status_code==200 and 'OFFICIAL_WEB' in response.text)
            summary=api.get('/api/knowledge/dungeons').json()
            check('三份实际官网快照可查询',summary['stats']['sources']==3 and summary['stats']['snapshots']>=3,summary['stats'])
            check('历史资料未标记当前官服已验证',summary['stats']['current_game_verified_facts']==0)
            check('官网知识未参与自动执行',all(not s['automatic_execution_allowed'] for s in api.get('/api/knowledge/dungeons/alama/stages').json()['items']))
            with sync_playwright() as p:
                browser=p.chromium.launch()
                page=browser.new_page(viewport={'width':1440,'height':1100})
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(args.url+'/#/knowledge')
                expect(page.get_by_role('heading',name='九星副本知识库',exact=True)).to_be_visible()
                expect(page.locator('.knowledge-facts article').first).to_be_visible()
                check('知识库页面显示真实数据库事实',page.locator('.knowledge-facts article').count()>10)
                page.locator('.evidence summary').first.click()
                expect(page.get_by_text('SHA-256',exact=False).first).to_be_visible()
                check('事实包含可展开的 URL 日期 原始值 定位信息',page.locator('.evidence[open] code').count()==1)
                options=page.locator('.version-select option')
                check('历史版本独立可选',options.count()>=3)
                old_option=next(o for o in options.all() if '2010' in o.inner_text())
                page.locator('.version-select select').select_option(old_option.get_attribute('value'))
                expect(page.locator('.fact-value').filter(has_text='850、500')).to_be_visible()
                check('2010 来源坐标单独显示',True)
                check('未知怪物数值未使用模拟器数据补全',all('生命 UNKNOWN' in item.inner_text() for item in page.locator('.boss-grid article').all()))
                page.screenshot(path=str(REPORT/'knowledge-page.png'),full_page=True)
                page.get_by_role('link',name='九星副本流程').click()
                expect(page.get_by_role('heading',name='九星副本流程',exact=True)).to_be_visible()
                expect(page.locator('.flow-list article')).to_have_count(5)
                check('流程显示入口和四个官方阶段',True)
                check('流程区分官方 观察 未确认 模拟器',all(t in page.locator('.boundary-grid').inner_text() for t in ['已有官方证据','GAME_OBSERVATION','尚未确认','SIMULATOR']))
                page.screenshot(path=str(REPORT/'knowledge-flow.png'),full_page=True)
                page.get_by_role('link',name='数据来源管理').click()
                expect(page.get_by_role('heading',name='数据来源管理',exact=True)).to_be_visible()
                expect(page.locator('.source-list>article')).to_have_count(3)
                check('来源管理显示真实来源与 SHA256',page.locator('.snapshot').count()>=3)
                check('冲突记录保留差异',page.locator('.conflict-list>article').count()>=2)
                page.screenshot(path=str(REPORT/'knowledge-sources.png'),full_page=True)
                page.reload()
                expect(page.locator('.source-list>article')).to_have_count(3)
                check('页面刷新恢复持久化知识数据',True)
                page.get_by_role('link',name='运行控制台',exact=False).first.click()
                expect(page.get_by_role('heading',name='游戏 Agent 控制台')).to_be_visible()
                check('原控制台展示 SIMULATOR 标识','SIMULATOR' in page.locator('.notice').inner_text())
                check('浏览器无 JavaScript 错误',not errors,errors)
                browser.close()
    except Exception as exc: checks.append({'name':'执行异常','passed':False,'details':repr(exc)})
    result={'timestamp':datetime.now(timezone.utc).isoformat(),'url':args.url,'checks':checks,'passed':bool(checks) and all(c['passed'] for c in checks),'browserErrors':errors}
    (REPORT/'knowledge-browser-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
