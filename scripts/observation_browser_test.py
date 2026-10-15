import argparse,json,time,sys
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright,expect
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.world import World
import cv2
ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT))
from scripts.test_artifacts import report_root, require_test_service
REPORT=report_root()
def main():
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8002');url=p.parse_args().url
    require_test_service(url)
    checks=[];errors=[]
    def check(name,ok):
        checks.append({'name':name,'passed':bool(ok)});assert ok,name
    try:
        with httpx.Client(base_url=url,timeout=30) as api,sync_playwright() as pw:
            browser=pw.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
            for route,title in [('observations','场景观察中心'),('media','截图与录像管理'),('analysis','OCR 和视觉分析'),('annotations','九星地图标注'),('boss-samples','BOSS 样本管理'),('review','知识库补全审核'),('twins','数字孪生场景管理'),('evidence','数据证据详情')]:
                page.goto(url+'/#/'+route);expect(page.get_by_role('heading',name=title,exact=True)).to_be_visible();check('中文页面 '+route,True)
            page.goto(url+'/#/media');raw=cv2.imencode('.png',World().render())[1].tobytes()
            page.locator('input[type=file]').set_input_files({'name':'authorized-simulator.png','mimeType':'image/png','buffer':raw})
            page.get_by_placeholder('说明所有权或授权依据；自建素材写明生成方式').fill('自建 World 渲染，仅用于隔离浏览器验收')
            page.get_by_role('checkbox').check();page.get_by_role('button',name='导入资料',exact=True).click()
            expect(page.locator('.media-metadata')).to_contain_text('SHA-256');check('浏览器真实导入并显示原图',page.locator('.media-image img').count()>0 if page.locator('.media-image').count() else page.locator('img').count()>0)
            media=api.get('/api/observations/media').json()['items'][0];mid=media['id'];check('导入原始文件一致',api.get('/api/observations/media/'+mid+'/file').content==raw)
            page.goto(url+'/#/analysis');page.get_by_role('button',name='执行本地视觉分析').click();expect(page.locator('.table-wrap tbody tr').first).to_be_visible(timeout=60000)
            result=api.get('/api/observations/media/'+mid).json()['observations'];check('OCR 和检测关联导入图像',all(r['media_id']==mid for r in result))
            check('实际检测角色区域',any(r['kind']=='PLAYER' and r['region']==[50,50,8,8] for r in result))
            page.goto(url+'/#/annotations');expect(page.get_by_role('button',name='保存新版本',exact=False)).to_be_enabled();svg=page.locator('svg').first;expect(svg).to_be_visible();svg.scroll_into_view_if_needed();b=svg.bounding_box();page.mouse.move(b['x']+80,b['y']+80);page.mouse.down();page.mouse.move(b['x']+180,b['y']+180);page.mouse.up();expect(page.locator('textarea')).not_to_have_value('[]');page.get_by_role('button',name='保存新版本',exact=False).click()
            expect(page.get_by_role('button',name='保存新版本（当前 1）')).to_be_visible();check('画框标注实际保存版本',len(api.get('/api/observations/media/'+mid).json()['annotations'])==1 and len(api.get('/api/observations/media/'+mid).json()['annotations'][0]['annotations'])==1)
            page.goto(url+'/#/review');page.locator('select').filter(has=page.locator('option[value=""]')).last.select_option('UNKNOWN');page.get_by_role('button',name='审核 / 纠错').first.click()
            page.get_by_placeholder('只能确认此画面确实显示的值').fill('人工核对原创模拟测试样本，只确认测试文字')
            page.locator('textarea').last.fill(json.dumps('测试可见文字'));page.get_by_role('button',name='提交人工审核').click()
            expect(page.get_by_role('button',name='提交人工审核',exact=True)).to_have_count(0)
            check('浏览器人工纠正 UNKNOWN 严格标记模拟假设',any(f['data_class']=='SIMULATED_ASSUMPTION' for f in api.get('/api/observations/facts').json()['items']))
            page.goto(url+'/#/twins');editor=page.locator('textarea');config=json.loads(editor.input_value());config['scene_id']='browser-research-'+str(time.time_ns());ann=api.get('/api/observations/media/'+mid).json()['annotations'][0];config['evidence_ids']=[ann['id']];config['map_regions']=[{'data_class':'SIMULATED_ASSUMPTION','evidence_ids':[ann['id']],'value':ann['annotations'],'assumption_note':'实际自建模拟器画面的人工标注，仅作测试','execution_environment':'SIMULATOR'}];config['validation_status']='EVIDENCE_LINKED';editor.fill(json.dumps(config,ensure_ascii=False));
            with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/api/observations/twins')) as saved:
                page.get_by_role('button',name='保存场景新版本').click()
            check('场景保存请求成功',saved.value.status==200)
            expect(page.get_by_role('button',name='加载此版本编辑').first).to_be_visible();check('浏览器保存独立场景草稿',any(r['scene_id']==config['scene_id'] for r in api.get('/api/observations/twins').json()['items']))
            expect(page.locator('.logs')).to_contain_text('TWIN_SCENE_UPDATED',timeout=10000);check('SSE 真实场景事件实时显示',True)
            logs=api.get('/api/logs',params={'search':'保存独立九星研究场景版本','limit':2000}).json();event=next(e for e in reversed(logs) if e['eventType']=='TWIN_SCENE_UPDATED')
            with api.stream('GET','/api/logs/stream',headers={'Last-Event-ID':str(event['id']-1)}) as response:
                for line in response.iter_lines():
                    if line.startswith('data: '):check('观察事件 SSE Last-Event-ID 补读',json.loads(line[6:])['id']==event['id']);break
            page.screenshot(path=str(REPORT/'v12-observation-page.png'),full_page=True)
            check('前端无运行异常',not errors);browser.close()
    finally:
        (REPORT/'v12-browser-results.json').write_text(json.dumps({'checks':checks,'errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
