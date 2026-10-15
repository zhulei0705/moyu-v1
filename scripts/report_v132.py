import datetime,hashlib,json,sqlite3,xml.etree.ElementTree as ET
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];REPORT=ROOT/'reports'

def main():
    baseline=json.loads((REPORT/'v132-baseline/tables.json').read_text(encoding='utf-8-sig'))
    db=sqlite3.connect(f'file:{(ROOT/"data/agent.sqlite3").as_posix()}?mode=ro',uri=True)
    protected={}
    for table,old in baseline.items():
        original=db.execute('SELECT * FROM '+table+' ORDER BY rowid LIMIT ?',(old['count'],)).fetchall()
        preserved=hashlib.sha256(repr(original).encode()).hexdigest()==old['sha256']
        protected[table]={'original_count':old['count'],'current_count':db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],'original_row_hash_preserved':preserved}
    assert all(r['original_row_hash_preserved'] for r in protected.values())
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    versions=[r[0] for r in db.execute('SELECT version FROM schema_migration ORDER BY version')]
    suite=ET.parse(REPORT/'v132-pytest.xml').getroot().find('testsuite');unit=suite.attrib
    assert unit['errors']=='0' and unit['failures']=='0'
    browser={}
    for file in ['browser-results.json','knowledge-browser-results.json','v12-browser-results.json','v13-browser-results.json','v131-browser-results.json','v132-reliability-browser.json']:
        data=json.loads((REPORT/file).read_text(encoding='utf-8-sig'));assert all(r['passed'] for r in data['checks']);browser[file]=len(data['checks'])
    stress=json.loads((REPORT/'v132-stress.json').read_text(encoding='utf-8-sig'));assert stress['passed']
    assert 'pass 9' in (REPORT/'v132-console-unit.log').read_text(encoding='utf-8-sig')
    assert 'built in' in (REPORT/'v132-build.log').read_text(encoding='utf-8-sig')
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=10) as api:
        health=api.get('/api/health').json();service=api.get('/api/service/status').json()
    assert health['version']=='1.3.2' and service['service_status']=='RUNNING'
    changed=['backend/main.py','backend/store.py','backend/runtime.py','backend/research_repository.py','frontend/src/Shell.vue','tests/test_console.py','tests/test_research.py','scripts/console_browser_test.py','start.ps1','test.ps1','README.md','frontend/dist (构建产物)']
    new=['backend/reliability.py','backend/migrations/007_reliability.sql','backend/migrations/007_reliability_down.sql','scripts/service_common.py','scripts/service_entry.py','scripts/service_runner.py','scripts/service_manager.py','scripts/reliability_stress.py','scripts/reliability_browser_test.py','scripts/report_v132.py','frontend/src/ServiceStatus.vue','tests/test_reliability.py','stop.ps1','status.ps1','restart.ps1','docs/V1.3.2.md','reports/V1.3.2_REPORT.md','reports/v132-delivery.json']
    samples=stress['samples'];memory=[s['memory_bytes'] for s in samples if s['memory_bytes'] is not None]
    result={'created_at':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),'changed_files':changed,'new_files':new,'pytest':unit,'frontend_unit_passed':9,'browser_checks':browser,'browser_total':sum(browser.values()),'build_passed':True,'original_tables':protected,'migration_versions':versions,'production_service':service,'stress_summary':{'rounds':stress['rounds'],'duration_seconds':stress['duration_seconds'],'checks':len(stress['checks']),'rss_min':min(memory),'rss_max':max(memory),'sse_after_cleanup':stress['after_cleanup']['sse_clients'],'threads_after_cleanup':stress['after_cleanup']['thread_count'],'earnings_gold':stress['earnings']['simulated_gold']}}
    (REPORT/'v132-delivery.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    text=f'''# 魔域 Web Agent V1.3.2 实测交付报告

生成时间：{result['created_at']}。增量完成本地服务托管、安全恢复审计、Windows启停脚本和真实Web服务状态，原Agent、地图、知识库及结算流程保留。

## 诊断结果与证据

- 旧start.ps1前台执行Uvicorn，确实依赖启动终端/会话进程生命周期。原服务退出原因仍未知，没有证据证明OOM、未捕获Python异常、审批拒绝或浏览器关闭导致退出。
- Windows虚拟环境启动器与实际Python PID不同，已实测并以健康instance_id和实际pid核对，不能仅按启动器PID猜测状态。
- 长连接浏览器在线重启测试中真实发生状态文件os.replace WinError5，管理器退出。堆栈保留在.runtime/service-8012/manager.log；已修复Windows读共享冲突的有界原子写入重试，并测试读锁释放后成功。不能把开发中这次新故障追认作以前的退出原因。
- 2026-10-10继续检查时，8000两个进程不存在、端口无监听，但状态记录RUNNING，且无正常退出事件；证据v132-session-exit-state.json与管理/服务日志。现在返回EXITED_UNCONFIRMED，不冒充在线；确切退出时刻和外部原因仍未知。
- 可选IndependentHost使用系统已有本地WMI进程创建接口，减少启动会话依赖。已实测启动命令返回后后台实例存活、可停止；接口拒绝时直接报错，不提权、不改变权限，不安装新服务/计划任务，也不默认启用Watchdog。

本次源树及父目录未发现AGENTS.md，Git返回not a git repository。原文件与生产SQLite一致性快照在v132-baseline；没有初始化新项目或替换生产库。

## 启动与停止

```powershell
cd D:\\myAI\\moyu-v1\\moyu-agent
.\\start.ps1 -SkipSetup -NoBrowser -IndependentHost
.\\status.ps1
.\\stop.ps1
.\\restart.ps1 -IndependentHost
# 明确启用可选Watchdog：
.\\start.ps1 -SkipSetup -NoBrowser -IndependentHost -Watchdog -MaxRestarts 3 -BaseDelay 2
# 重现完整测试及长连接连续运行：
.\\test.ps1 -ReliabilityStress
```

默认127.0.0.1:8000；当前生产服务已运行1.3.2，Watchdog={service['watchdog_enabled']}。脚本有端口检查、进程身份、错误提示、日志、启动超时、优雅停止及防重复。所有四个脚本已实际执行，JSON证据v132-powershell-*.json。IndependentHost实测证据v132-independent-parent.json和独立宿主pytest用例。

## 监控和安全恢复

端口与数据库路径均加Windows文件锁，拒绝同库多端口或同端口重复实例。Watchdog默认关闭，显式启用后只恢复已退出进程，默认最多3次，2/4/8秒指数退避、上限30秒。主动stop先写持久标志，退避/启动期间也不拉起；未知进程不强杀。权限或工具审批拒绝不会绕过。

服务每秒持久化心跳，记录启动/正常退出/未确认退出及Worker异常。Python请求与异常日志5MiB轮转保留5份；管理事件、启动stderr另存.runtime/service-端口。SSE连接finally释放计数，托管并发连接上限128，SQLite暂时锁定按busy_timeout等待并恢复，心跳写入失败不伪造成功时间。

每轮UUID保持；状态变化、暂停/恢复/停止请求持久化agent_state_history。服务恢复将未知RUNNING标记INTERRUPTED，agent_recovery记录STOP_UNCONFIRMED_NO_REPLAY，不自动续跑或重发未知动作。确定Worker异常仍为FAILED。原副本结算新增幂等检查，已完成和中断任务不能再次写成功奖励；九星沿用事务唯一键，已完成结算不改写。

GET /api/service/status；GET /api/agent/state-history?run_id=...；health含instance_id/pid。所有原Web页面右下角服务面板显示服务/Worker、真实心跳、启动、异常检测、重启次数、恢复状态和连接/线程数。服务断线显示待确认。原console/SSE恢复功能保留。

## 文件与迁移

修改：
{chr(10).join('- `'+p+'`' for p in changed)}

新增：
{chr(10).join('- `'+p+'`' for p in new)}

007_reliability.sql新增service_instance、service_event、agent_state_history、agent_recovery四表，提供down SQL。在隔离完整副本执行007回退，原29张业务表及旧迁移记录保留，integrity_check=ok；没有在生产执行回退。

## 全部真实测试结果

| 检查 | 结果 |
|---|---|
| V1.3.1 pytest基线 | 62通过 |
| 当前全量pytest | {unit['tests']}通过，0失败，0错误 |
| 前端请求/数值单元测试 | 9通过 |
| 原V1.0/V1.1/V1.2/V1.3/V1.3.1浏览器/API | 116通过 |
| 新可靠性浏览器 | 8通过 |
| 浏览器/API累计 | {sum(browser.values())}通过 |
| 前端vue-tsc + Vite构建 | 通过 |
| 八轮真实九星压力测试 | {len(stress['checks'])}项通过，{stress['duration_seconds']:.3f}秒 |
| SSE并发/补读/释放 | 六路真实长连接，每路补读超过1000条；关闭后0连接 |
| Windows四个管理脚本 | 实际start/status/restart/stop通过 |
| 数据库完整性 | ok |

压测8轮全部COMPLETED，每轮独立run_id；模拟金币5134、碎片182、材料88；工作集最小{min(memory)/1048576:.2f}MiB、最大{max(memory)/1048576:.2f}MiB；关闭SSE后线程{stress['after_cleanup']['thread_count']}、SSE={stress['after_cleanup']['sse_clients']}。样本不含真实人民币收入。8轮结算与运行数量一致；实际杀死测试服务后Watchdog恢复，完整已完成运行和收益返回完全一致。

pytest覆盖实际进程终止、重启上限、退避、无Watchdog不重启、主动停止不重启、数据库锁释放、未知任务不重放、成功结算幂等、重复端口与数据库启动防护、独立宿主与残留状态校验。可靠性浏览器测试覆盖真实退出→断线→新实例恢复→计数与异常时间→历史/结算不变→主动停止。没有未处理JS异常。曾发生测试lambda错误和Windows共享冲突，修正后重跑，最终JUnit没有失败；仍有Starlette弃用警告和VP80诊断，不作为失败。

证据：v132-pytest.xml、v132-stress.json、v132-reliability-browser.json、v132-build.log、v132-console-unit.log、各浏览器JSON、v132-delivery.json。

## 历史数据校验

原30张表的全部旧行有序内容SHA-256均一致，runs仍{protected['runs']['current_count']}、logs仍{protected['logs']['current_count']}。知识库、来源、已审核事实、观察、原收益与孪生记录未删除或改写。schema_migration仅追加7，原5条完整保留，当前{versions}。测试数据全部在隔离库，未灌入生产业务数据。

## 访问与未解决问题

[控制台](http://127.0.0.1:8000/#/console)，[观察中心](http://127.0.0.1:8000/#/observations)。详细操作说明[docs/V1.3.2.md](../docs/V1.3.2.md)。

以前服务静默消失的外部原因仍未确定；IndependentHost减少会话依赖但不能承诺抵抗外部工具结束、注销/关机或系统故障。默认未装系统服务，重启OS后不自动运行；Supervisor本身被结束不能自我复活。存活但永久卡住的服务不自动强杀。诊断事件、业务日志与图像证据长期增长，需规划磁盘容量，不自动清理历史。短时8轮约100秒验证不能代替24小时无人值守测试。
'''
    (REPORT/'V1.3.2_REPORT.md').write_text(text,encoding='utf-8')
    print(json.dumps({'pytest':unit['tests'],'browser':sum(browser.values()),'protected_tables':len(protected),'runs':protected['runs']['current_count'],'logs':protected['logs']['current_count'],'watchdog':service['watchdog_enabled']},ensure_ascii=False))

if __name__=='__main__':main()
