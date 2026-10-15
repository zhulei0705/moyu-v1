"""Generate delivery only from executed test artifacts and current read-only checks."""
import datetime,hashlib,json,sqlite3,xml.etree.ElementTree as ET
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1];REPORT=ROOT/'reports'
def main():
    baseline=json.loads((REPORT/'v131-baseline/tables.json').read_text())
    db=sqlite3.connect(f'file:{(ROOT/"data/agent.sqlite3").as_posix()}?mode=ro',uri=True)
    preserved={}
    for table,old in baseline.items():
        rows=db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()
        digest=hashlib.sha256(repr(rows).encode()).hexdigest()
        preserved[table]={'count':len(rows),'sha256':digest,'unchanged':len(rows)==old['count'] and digest==old['sha256']}
    assert all(r['unchanged'] for r in preserved.values())
    integrity=db.execute('PRAGMA integrity_check').fetchone()[0];assert integrity=='ok'
    versions=[r[0] for r in db.execute('SELECT version FROM schema_migration ORDER BY version')]
    suite=ET.parse(REPORT/'v131-pytest.xml').getroot().find('testsuite')
    assert suite is not None and suite.attrib['failures']=='0' and suite.attrib['errors']=='0'
    pytest=suite.attrib
    checks={}
    for file in ['browser-results.json','knowledge-browser-results.json','v12-browser-results.json','v13-browser-results.json','v131-browser-results.json']:
        data=json.loads((REPORT/file).read_text(encoding='utf-8-sig'))
        assert all(r['passed'] for r in data['checks'])
        checks[file]={'count':len(data['checks']),'passed':True}
    unit=(REPORT/'v131-console-unit.log').read_text(encoding='utf-8-sig')
    assert 'pass 9' in unit and 'fail 0' in unit
    build=(REPORT/'v131-build.log').read_text(encoding='utf-8-sig');assert 'built in' in build
    api=[]
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=10) as client:
        for path in ['health','agent/status','dungeon/status','inventory','earnings','scenes','runs','logs?limit=1&tail=true','research/decisions?limit=1','research/earnings','research/settlements']:
            response=client.get('/api/'+path);response.raise_for_status();api.append({'path':'/api/'+path,'status':response.status_code,'response':response.json()})
        response=client.get('/api/frame/latest');assert response.status_code==200 and response.content[:8]==b'\x89PNG\r\n\x1a\n';api.append({'path':'/api/frame/latest','status':200,'bytes':len(response.content),'format':'PNG'})
    assert api[0]['response']['version']=='1.3.1'
    changed=['frontend/src/App.vue','backend/main.py','backend/runtime.py','test.ps1','README.md','frontend/dist (前端构建产物)']
    new=['frontend/src/console.ts','frontend/tests/console.test.mjs','tests/test_console.py','scripts/console_browser_test.py','scripts/report_v131.py','reports/V1.3.1_REPORT.md','reports/v131-delivery.json']
    summary={'generated_at':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),'pytest':pytest,'frontend_unit':{'passed':9,'failed':0},'browser':checks,'browser_total':sum(r['count'] for r in checks.values()),'build_passed':True,'data_integrity':preserved,'sqlite_integrity':integrity,'migration_versions':versions,'production_api':api,'changed_files':changed,'new_files':new}
    (REPORT/'v131-delivery.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    text=f'''# 魔域 Agent V1.3.1 控制台稳定性修复实测报告

生成时间：{summary['generated_at']}。本轮仅修复控制台连接、控制确认和数值展示，不提前开发V1.4；Agent、模拟器、知识库、结算算法与原数据库业务记录保留。

## 根因与复现

1. Failed to fetch：接手时Get-NetTCPConnection检查8000未监听，Invoke-RestMethod连接被拒绝。前端请求为同源 `/api/`，Vite代理到8000，FastAPI实际路由一致；没有发现错误Base URL或CORS原因。旧界面直接String(error)输出原始TypeError；onMounted依次await场景/历史请求，任何首次连接错误都会阻断后续轮询与SSE初始化，且Promise异常未处理。SSE状态还混作整体连接状态，轮询恢复后旧错误仍保留。服务此前退出的具体原因没有足够日志可追溯，不能推断为Agent崩溃。
2. NaN：尚未创建world时dungeon接口没有bag_capacity/bag_used，选择九星页面后直接 `undefined - undefined`，于是显示NaN和空容量。多项指标还用??0或默认攻击25掩盖缺失值，未经验证调用toFixed存在非数字返回导致渲染异常的风险。

真实浏览器复现证据：[原界面截图](v131-baseline/reproduced.png)、[控制区和Network失败记录](v131-baseline/reproduction.json)。复现记录同时包含NaN、TypeError: Failed to fetch和net::ERR_CONNECTION_REFUSED。API地址和代理已逐项检查，保留同源设计与本机控制校验，没有放宽CORS。

## 具体修复

- 新console.ts统一同源请求、AbortController超时、中文网络/HTTP/格式错误；普通查询4秒、控制命令10秒。POST不自动重试，超时或响应丢失后只查询真实状态。
- API与SSE连接状态分开显示，记录最后成功连接时间。首次错误不会阻止800ms轮询；部分业务数据失败单独提示。断线保留最后已知数据并明确标注状态待确认，不改变后台任务。
- 状态必须包含真实running、paused、state、scene_id字段，未知状态不能当作IDLE。并发状态响应按请求序号处理，避免旧响应覆盖新状态。
- 控制前查询状态，busy阻止双击重复POST；控制后再次GET验证running、runId、paused或实际结束。HTTP200不能当作启动成功。失败提供“重新同步 / 重试连接”入口，确认状态后用户可再次操作。
- 未创建场景的API生命/攻击/背包/动作字段返回null和loaded=false，避免虚构零值。统一拒绝字符串、布尔、非有限数、负数；缺失显示“待获取”，不可计算显示“暂无有效数据”；数据库或实际世界确认的0保留。
- 容量0/占用0正确显示0/0；容量小于占用显示无效。无通关样本或耗时0不能计算平均时间、收益效率。进度数值有限且显示上限100%。明确标示画面与本轮指标的实际场景，场景选择器用于下一次启动。
- EventSource自动恢复，后端Last-Event-ID补读接口保持；界面日志去重、批量更新、最多500行，未展开不渲染大JSON。不会截断SQLite日志或SSE持久化记录。

## 实际修改文件

修改：
{chr(10).join('- `'+p+'`' for p in changed)}

新增：
{chr(10).join('- `'+p+'`' for p in new)}

test.ps1集成新的前端单元测试及控制台浏览器测试，PowerShell解析检查无错误。没有数据库迁移；schema_migration仍为{versions}，没有清空、重建或重生成业务数据。目录没有Git仓库，项目与父目录未发现AGENTS.md；修复前已保存被修改源码、完整SQLite一致性副本和30张表哈希到v131-baseline，未覆盖其他用户文件。

## 实测结果

| 验证 | 结果 |
|---|---|
| 修改前V1.3 pytest基线 | 58项通过 |
| 修改后全量pytest | {pytest['tests']}项通过，0失败，0错误 |
| 新前端单元测试 | 9项通过 |
| 原控制台浏览器/API | {checks['browser-results.json']['count']}项通过 |
| 官网知识浏览器/API | {checks['knowledge-browser-results.json']['count']}项通过 |
| V1.2观察浏览器/API | {checks['v12-browser-results.json']['count']}项通过 |
| V1.3九星浏览器/API | {checks['v13-browser-results.json']['count']}项通过 |
| V1.3.1连接与指标浏览器 | {checks['v131-browser-results.json']['count']}项通过 |
| 浏览器/API总计 | {summary['browser_total']}项通过 |
| vue-tsc + Vite生产构建 | 通过 |
| 当前8000生产只读API核验 | 12项HTTP200，包括真实PNG |
| SQLite完整性 | {integrity} |
| 所有原表数据 | {len(preserved)}张表数量与完整行内容SHA-256保持一致 |

新增浏览器覆盖正常连接、首次后端不可达、AbortController超时、启动HTTP500、HTTP200但实际Worker未启动、空/非数字字段、容量0、双击和409、刷新恢复暂停、UI断网保持后端任务、SSE恢复与去重、500行上限、从真实旧游标补读数千条日志、真实服务进程退出与重新启动、完整历史保留，无未处理JS异常。错误响应与网络故障使用浏览器拦截，正常动作和大量日志来自真实隔离模拟器运行。重启测试启动自己的8006进程并使用独立SQLite，不终止生产Worker。

生产API详情见[v131-delivery.json](v131-delivery.json)。实际POST启动/暂停/恢复/停止、409重复启动、403跨站请求和失败后Worker状态均在隔离服务或TestClient中验证；生产核验只读。pytest第一次使用系统临时目录遇到WinError5，改用reports下隔离basetemp后58项基线与62项回归全部通过，没有修改断言。仍有Starlette测试客户端弃用警告与WebM VP80容器诊断，不影响通过结果。

## 数据保护

生产历史runs={preserved['runs']['count']}、logs={preserved['logs']['count']}、fact_evidence={preserved['fact_evidence']['count']}、官网冲突={preserved['data_conflict']['count']}，全部哈希不变；来源、已审核事实、观察、孪生、研究配置、原收益记录及schema_migration均校验。测试使用基线副本和独立数据库，不写入生产业务记录。

## 访问与尚未解决事项

[运行控制台](http://127.0.0.1:8000/#/console)；[九星副本运行](http://127.0.0.1:8000/#/research)。8000已运行V1.3.1；生产启动日志v131-production-error.log和请求日志v131-production.log已保留。已通过实际浏览器复核，截图[v131-production-console.png](v131-production-console.png)。

已复现的连接处理和NaN缺陷均已修复。后端停止期间页面能显示断线并自动恢复，但页面不能替代启动后端服务；服务此前退出的具体原因尚无法确认。本轮没有安装Windows服务管理器或新增后端自动拉起机制。九星地图/战斗/掉落仍按V1.3标记模拟假设；与本轮控制台稳定性无关，未扩展。
'''
    (REPORT/'V1.3.1_REPORT.md').write_text(text,encoding='utf-8')
    print(json.dumps({'pytest':pytest['tests'],'frontend_unit':9,'browser':summary['browser_total'],'production_table_hashes':len(preserved),'history_runs':preserved['runs']['count'],'history_logs':preserved['logs']['count']},ensure_ascii=False))

if __name__=='__main__':main()
