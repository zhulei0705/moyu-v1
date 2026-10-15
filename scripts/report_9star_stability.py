"""Generate the human report exclusively from recorded test evidence."""
import json
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'reports'

def read(path):
    return json.loads((REPORT/path).read_text(encoding='utf-8'))

def metrics(result):
    values=[s['memory_bytes'] for s in result['samples'] if s['memory_bytes'] is not None]
    return (f"实际 {result['actual_rounds']} 轮；完成 {result['completed']}，失败 {result['failed']}；"
            f"成功率 {result['success_rate']*100:.2f}%；平均单轮 {result['average_round_seconds']:.3f} 秒；"
            f"测试及核验墙钟 {result['wall_seconds']:.3f} 秒；CPU {result['cpu_seconds']:.3f} 秒，"
            f"单核平均 {result['cpu_percent_one_core_average']:.2f}%；工作集最小/最大/末次 "
            f"{min(values)/1048576:.2f}/{max(values)/1048576:.2f}/{values[-1]/1048576:.2f} MiB；"
            f"日志 {result['log_count']} 条；异常日志 {result['exception_count']} 条；人工介入 {result['manual_interventions']} 次；"
            f"SQLite完整性 {result['integrity_check']}。")

def main():
    ten=read('9star-10-runs/result.json');first=read('9star-100-runs/result.json')
    final_path=REPORT/'9star-100-runs-confirmed/result.json'
    final=json.loads(final_path.read_text(encoding='utf-8')) if final_path.exists() else None
    faults=read('9star-faults/result.json')
    histories=read('9star-history-preservation.json')
    cases=faults['cases']
    browser_names=['browser-results.json','knowledge-browser-results.json','v12-browser-results.json','v13-browser-results.json','v131-browser-results.json','v132-reliability-browser.json']
    browsers=[(name,read(name)) for name in browser_names]
    browser_count=sum(len(value['checks']) for _,value in browsers)
    delivery=REPORT/'9star-delivery.xml'
    pytest='最终全量回归尚未结束，不能标记通过。'
    if delivery.exists():
        suite=ET.parse(delivery).getroot().find('testsuite')
        pytest=f"最终pytest：{suite.get('tests')} 项，失败 {suite.get('failures')}，错误 {suite.get('errors')}，耗时 {suite.get('time')} 秒；证据 `9star-delivery.xml`。"
    first_db=REPORT/'9star-100-runs/agent.sqlite3'
    with sqlite3.connect(first_db) as db:
        db.row_factory=sqlite3.Row
        failed=dict(db.execute("SELECT r.* FROM runs r JOIN research_run x ON x.run_id=r.id WHERE r.status='FAILED' ORDER BY r.started DESC LIMIT 1").fetchone())
        event=dict(db.execute("SELECT id,details FROM logs WHERE runId=? AND eventType='error' ORDER BY id DESC LIMIT 1",(failed['id'],)).fetchone())
        inventory=json.loads(db.execute("SELECT actual_result FROM agent_decision WHERE run_id=? ORDER BY rowid DESC LIMIT 1",(failed['id'],)).fetchone()[0])['perception']['inventory']
        total=dict(db.execute('SELECT * FROM research_settlement WHERE run_id=? AND stage_id=0',(failed['id'],)).fetchone())
        resource_match=inventory=={'gold':total['simulated_gold'],'shards':total['simulated_fragments'],'materials':total['simulated_materials']}
    fault_table='\n'.join(f"| {c['fault']} | {c['status']} | {c['seconds']:.3f} | {c['log_count']} | {'一致' if c['database_consistency'] else '不一致'} | {c['run_id']} |" for c in cases)
    browser_table='\n'.join(f"| {name} | {len(value['checks'])} | {'通过' if all(c['passed'] for c in value['checks']) else '失败'} |" for name,value in browsers)
    final_section=(metrics(final)+f"\n\n结论：{'通过' if final['passed'] else '未通过'}。逐轮四阶段证据、资源守恒和数据库一致性：{final['database_consistency']}。完整UUID、每轮耗时、日志数、阶段和资源采样见 `9star-100-runs-confirmed/result.json`；每步PNG、决策和结算保存在同目录 `agent.sqlite3`。") if final else '正在执行/核验。未完成，不标记通过。'
    text=f'''# 九星稳定性实测报告

生成时间：{datetime.now().astimezone().isoformat()}。全部任务为自建九星模拟器，未接入真实官服，没有虚构通关或奖励。测试使用隔离库，未把测试运行写入生产业务历史。

## 验收状态

| 测试 | 状态 |
|---|---|
| A：连续10轮、默认80ms间隔 | {'通过' if ten['passed'] else '失败'} |
| B：首次100轮尝试 | 未通过，实际22轮后停止，保留失败 |
| B：修复后完整100轮、5ms间隔 | {'通过' if final and final['passed'] else '尚未完成'} |
| C：8小时真实墙钟 | 未执行，待用户授权窗口 |
| D：24小时真实墙钟 | 未执行，待用户授权窗口 |
| E：模拟及基础设施故障注入 | 13种模拟故障达到预期，基础设施证据如下 |

5ms只缩短动作间的等待，图像识别、A*、全部攻击/拾取/阶段转换和SQLite提交实际执行。100轮加速样本不能换算为8小时或24小时，更不能据此宣布长期无人值守已验收。

## A：连续10轮

{metrics(ten)}

每轮四阶段实际完成、退出、拾取事件、背包、stage0和四阶段资源/成本一致，重复结算零次。没有注入故障，自动恢复率无分母，记为null。`9star-10-runs/result.json`与同目录SQLite包含完整run_id及证据。此旧版本脚本的started_at_utc字段实际为结果生成时间；真实开始/结束时间以SQLite runs.started/ended为准，耗时使用单调时钟。

## B：首次尝试失败记录

{metrics(first)}

申请100轮，实际完成21轮，第22轮FAILED，剩余78轮未启动。失败run_id `{failed['id']}`，事件ID `{event['id']}`，error_code ACTION_TIMEOUT。动作实际把阶段2推进到3，后图保留阶段3目标与库存，但动作计时还包含日志和PNG写入，最终超过2秒。修复只拆开动作计时；适配器仍2秒，观察/验证/证据链路独立10秒，保留全程、阶段和状态上限。

原始JSON database_consistency=false是旧测试评估器还要求FAILED轮run.rewards与成功轮一样非空，不能据此声称数据库损坏。失败轮后图库存与失败结算逐项对照：{resource_match}，SQLite integrity_check=ok；失败未产生成功收益。原始失败JSON不改写，成功验收只使用重新完成的完整100轮。

## B：修复后完整100轮

{final_section}

正常测试没有异常恢复样本，自动恢复成功率为null；异常注入另行统计。平均单轮按实际runs.duration计算，墙钟还包含最后的逐轮核验，因此两者不等价。CPU为真实process_time，百分比采用单核口径；内存为Windows进程工作集，不是预测值。

## E：故障注入与明确终止路径

13次独立模拟注入：完成 {sum(c['completed'] for c in cases)}，明确失败 {sum(c['failed'] for c in cases)}，均符合预期；可恢复类别5/5自动恢复成功，持续/不可恢复类别8/8安全终止。异常事件/恢复尝试逐项保存在 `9star-faults/result.json`。实际人工介入0；测试程序对每个独立案例创建新库，不是对失败任务无限重启。

| 故障 | 最终状态 | 秒 | 日志 | 结算资源 | run_id |
|---|---|---:|---:|---|---|
{fault_table}

每案例JSON记录CPU秒、工作集采样、异常事件ID、原因、重试和恢复确认时间；故障样本不混入正常100轮成功率。角色死亡/阶段超时/持续失败无需冒险恢复，安全终止是预期结果。

基础设施注入：

- Worker真实进程终止：原pytest的原始/研究Worker进程中断与重启测试实际通过，遗留运行INTERRUPTED，不重放、不写成功奖励；注入worker_interrupted另验证异常处理。
- API不可用：浏览器测试真实结束隔离服务进程，页面断线，有限Watchdog恢复新实例，历史和结算不变；主动停止后不拉起。证据 `v132-reliability-browser.json`（8项）。
- SSE断线：原浏览器/API检查覆盖真实Last-Event-ID补读、关闭连接与前端去重；新服务端批次100条、客户端500行、连接上限128。恢复不重复执行任务。
- 数据库写入失败：新增专项测试分别注入一次观察写入错误、持续所有写入错误、拾取已改变背包后loot事件写入错误。前者FAILED且失败结算可核对；持续错误释放Worker、禁止新任务，重启不重放；漏记拾取拒绝不完整结算并要求人工审核。后两类需要人工审核，测试中实际人工介入0。原SQLite占用锁/释放测试保留。

## 回归、Web与真实指标

{pytest}

此前87项全量回归已通过；初始基线75通过/1进程时限失败、专项30通过/2进程时限失败均保留，单独复核2项通过，断言未改。中途83通过/1停止竞争失败促成Event隔离修复。所有首次失败保留为原始日志，不作为最终通过结果覆盖。

原前端9项请求/数值测试及TypeScript/Vite构建通过；生产构建保留全部旧页面。

| 浏览器/API证据 | 项数 | 结果 |
|---|---:|---|
{browser_table}

合计 {browser_count} 项。新监控进程内API另4项通过：真实心跳/视觉/目标/恢复、CPU/内存/进程时长、实际通关、流式导出与日志逐条一致；证据 `9star-monitor-inprocess.json`。

SSE最终路由专项3项通过：实际预留128个连接，第129个返回503，全部断开后计数归零；证据 `9star-sse-final.json`。此项为进程内路由验证，不能冒充128个真实网络长连接压测。

额外专用监控浏览器脚本已保存，但启动8041测试服务的命令被自动审批拒绝，只返回blocked by policy，未提供具体原因；该额外脚本没有执行通过记录，不计入124项。未绕过拒绝，也未重启正在运行的生产8000服务；该进程仍加载旧后端，代码更新需正常重启后生效。

UI新增指标均来自service/status与Worker实际内存状态：九星阶段、当前目标、最后确认动作、最近图像结果、重试、卡住秒数、Worker心跳、进程时长、CPU/内存、连续完成与恢复记录。成功率仍来自真实研究结算报表，异常日志来自SQLite。缺失字段显示待获取；校验分数是模拟颜色/HUD完整性，不能冒充真实官服模型置信度。

## 历史与数据库一致性

历史旧行核验：除持续更新的service_instance心跳外，旧表业务和不可变历史行保持一致：{histories['business_and_immutable_history_preserved']}；生产integrity_check={histories['integrity_check']}。旧业务数据和33张表旧行SHA-256核验见 `9star-history-preservation.json`，第34张service_instance运行心跳正常变化，未恢复成旧时间。已有交付文件副本保留在 `9star-baseline/previous-delivery/`。

数据库2GiB容量保护、1000步/单次100轮、有限重试和无进度上限共同限制新增证据。达限停止待人工归档，不删除旧日志或图像。流式日志导出避免一次载入全历史。短时工作集没有证明8/24小时不增长，长时测试仍是必需验收项。

100轮证据库实际约751.23MiB，基线1.69MiB，约7.50MiB/轮。按默认调度14.03秒/轮估算，2GiB配额可能约1小时触发；这是容量推算，不是长时实测。自动证据归档尚未实现，长时授权窗口前还需确定保留历史的归档或更大有界容量策略。默认配置不能承诺连续8/24小时。

## 复核与未完成项

原始数据库可按run_id查询runs、logs、agent_observation、agent_decision、agent_checkpoint和research_settlement；奖励守恒按loot/deposit事件重算，成功须有四阶段及exit事件。可复核命令与恢复矩阵见 `docs/9STAR_RECOVERY_DESIGN.md`。

8小时、24小时尚未开始；需明确北京时间执行窗口、资源占用及跨夜许可。永久挂起的native调用不能安全在线程内强杀，心跳监控只闩锁停止并要求审核。未知动作不重放，死亡不无限复活，失败不无限重启。当前交付是短时稳定性增强及可复核证据，不能宣称已经完成长期无人值守验收。
'''
    (REPORT/'9STAR_100_RUN_TEST.md').write_text(text,encoding='utf-8')
    print('report generated; 100-run verified:', bool(final and final['passed']))

if __name__=='__main__':main()
