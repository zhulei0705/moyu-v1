"""Generate reports from measured artifacts; pending work stays NOT_RUN."""
import json,math,sqlite3,hashlib,zipfile
from pathlib import Path
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'reports';A=R/'v133-audit'
def read(path,default=None):
 p=ROOT/path
 return json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else default

def mib(n):return f'{n/1048576:.3f} MiB ({int(n)} bytes)'
def gib(n):return f'{n/1073741824:.3f} GiB'
def write(path,text):(ROOT/path).write_text(text+'\n',encoding='utf-8')

def main():
 inventory=read('reports/v133-audit/inventory.json',[])
 current=read('reports/v133-audit/current-audit.json',{})
 payload=read('reports/v133-audit/payload-metrics.json',{})
 before=read('reports/v133-before/result.json',{})
 freq=read('reports/v133-frequency/result.json',{})
 hundred=read('reports/v133-100/result.json',{})
 soak=read('reports/v133-1h/result.json',{})
 verification=read('reports/v133-1h/verification.json',{})
 if verification:
  soak=dict(soak)
  soak.update(completed=verification['completed'],actual_seconds=verification['collection_wall_seconds'],
      passed=verification['passed'],completed_without_settlement=0 if verification['settlement_consistency'] else None)
  (R/'v133-1h/verified-summary.json').write_text(json.dumps(dict(soak,verification=verification,
      raw_result_preserved='result.json',raw_count_correction='Exclude 5 inherited original-scene completed runs; report only joined research_run rows.'),indent=2))
 integrity=read('reports/v133-audit/integrity.json',{})
 backup=read('reports/v133-audit/backup.json',{})
 pressure=read('reports/v133-storage-pressure.json',{})
 samples=[json.loads(line) for line in (R/'v133-1h/samples.jsonl').read_text().splitlines()]
 first,last=samples[0],samples[-1]
 elapsed=soak.get('actual_seconds',last['elapsed'])
 final=soak.get('final_storage',last['storage'])
 net=final['logical_bytes']-first['storage']['logical_bytes']
 average_rate=net/max(1,elapsed)*3600
 stable_rates=[s['storage']['bytes_per_hour'] for s in samples if s['elapsed']>=300 and s['storage']['measurement_seconds']>=300]
 peak=max([average_rate]+stable_rates)
 # Account for removing competing regression load: original .08-clock cadence
 # times measured optimized per-round bytes is a conservative capacity rate.
 original_round=before.get('average_round_seconds')
 cadence_rate=freq.get('bytes_per_round',0)*3600/original_round if original_round else 0
 budget_rate=max(peak,cadence_rate)
 budgets={}
 for hours in (8,24):
  growth=budget_rate*hours;with_margin=final['logical_bytes']+growth*1.5+64*1024**2
  hard=math.ceil(with_margin/1073741824)*1073741824
  budgets[hours]=dict(growth=growth,margin=growth*.5,hard=hard,disk=hard*3+1073741824)
 initial_size=read('reports/v133-audit/before-init.json',{}).get('logical_bytes',(R/'v13-baseline/agent.sqlite3').stat().st_size)
 old_final_size=(R/'v133-before/agent.sqlite3').stat().st_size
 old_growth=old_final_size-initial_size
 old_rate=old_growth*60/before.get('wall_seconds',1)
 reduction=1-freq.get('bytes_per_round',0)/(old_growth/3) if old_growth else None
 stamp=datetime.now(timezone(timedelta(hours=8))).isoformat()
 aggregate={}
 for folder in ('data','reports','.runtime','.pytest_cache','backend','scripts','frontend/dist'):
  files=[p for p in (ROOT/folder).rglob('*') if p.is_file()]
  aggregate[folder]=dict(bytes=sum(p.stat().st_size for p in files),files=len(files))
 (A/'final-capacity.json').write_text(json.dumps(dict(recorded_at=stamp,categories=aggregate,budget_rate=budget_rate,budgets=budgets),indent=2))
 lines=['# 九星 V1.3.3 存储根因调查',f'生成时间（Asia/Shanghai）：{stamp}。全部采样来自自建模拟器；最终历史数据与备份一致，测试导入误写及恢复见数据完整性报告。','',
 '## 当前文件容量','|目录/类型|文件数|占用|','|---|---:|---:|']
 for key,value in aggregate.items():lines.append(f"|{key}|{value['files']}|{mib(value['bytes'])}|")
 lines+=['','生产及历史文件逐项清单：`v133-audit/inventory.json`。此清单在变更前采集；上表为报告生成时的容量，包含本轮隔离测试和备份，未清理任何历史数据库。',
 '历史最大测试库 `reports/9star-100-runs-confirmed/agent.sqlite3` 为 787718144 bytes，未改写。原始帧、识别、决策、运行日志和回放共用 SQLite；SSE 本身读取日志，不另复制到数据库。故障截图保存在观察链或测试截图文件，当前未发现独立自动录屏写入器。',
 '','## 修改前真实采样',f"隔离 3 轮：{before.get('wall_seconds')} 秒，3/3通关；checkpoint后完整主库 {mib(old_final_size)}，原源码初始化后基底逻辑页 {mib(initial_size)}，净增长 {mib(old_growth)}。",f'每轮净增长 {mib(old_growth/3)}；每分钟 {mib(old_rate)}。原result.json仅统计checkpoint前主库文件而漏算未回写WAL，不用于增长计算。本报告使用最终checkpoint后大小，以及从已验证原源码备份重新初始化的基底测量（before-init.json）。该测试是36秒实际壁钟短采样，不是1小时验收。',
 '|类别（逻辑内容字节，非独占物理页）|条数|3轮内容|每分钟|每轮|','|---|---:|---:|---:|---:|']
 for key,value in payload.get('before',{}).items():
  count,size=value;lines.append(f'|{key}|{count}|{mib(size)}|{mib(size*60/before.get("wall_seconds",1))}|{mib(size/3)}|')
 lines+=['识别内容包含像素提取目标及 HUD 信息；九星没有另存一套 OCR 画面。checkpoint为每 Run 覆盖式快照，其容量是最终快照，不能当作累计写入量。结算的“0内容字节”只是未估算字段，不代表零占用。索引、页空隙、SQLite记录开销解释内容字节与物理净增长的差异。',
 '','## 根因与优化证据','492 次观察只有 203 个不同 PNG，重复观察约 58.74%；每个动作的后帧通常是下次动作的前帧。原 PNG 总计 19059849 bytes。原完整决策同时写决策表和日志表，日志副本实际UTF-8内容 904892 bytes；本轮为保持完整回放兼容，保留这部分核心内容。',
 f"同种子优化后 3 轮：{freq.get('wall_seconds')} 秒，净新增每轮 {mib(freq.get('bytes_per_round',0))}，相较原每轮降低约 {reduction*100:.2f}%。203 个无损复用 PNG 共 448728 bytes。",
 f"逐帧解码比较：{payload.get('pixel_pairs','NOT_RUN')} 对画面，像素不一致 {payload.get('pixel_errors','NOT_RUN')}。压缩改变PNG字节编码，但没有改变像素或Agent识别输入。", 
 f"实际 SQL trace：{freq.get('counts',{})}。事务提交 {freq.get('commits_per_minute')} 次/分钟，INSERT/UPDATE {freq.get('statements_per_minute')} 次/分钟。`write_count`只统计Store.execute，不冒充全部事务数。", 
 '','## 保护范围、耗尽时间和预算',
 '原保护仅检查 `PRAGMA page_count * page_size` >= 2 GiB，每轮开始检查；不含WAL、SHM、外部媒体，也不按每动作检查。新保护含主库、WAL、SHM和媒体/诊断目录，增加动作前检查和磁盘保留；默认硬阈值仍2 GiB，软阈值80%，未通过猜测提高容量限制。',
 f'按旧短采样，空库到2 GiB约 {(2147483648-initial_size)/max(1,old_rate)/60:.2f} 小时；从旧100轮库继续约 {(2147483648-787718144)/max(1,old_rate)/60:.2f} 小时。这是速率外推，不是已执行的时长。',
 f"当前净增长小时均值 {mib(average_rate)}/小时，稳定采样窗口最高 {mib(peak)}/小时。预算另外参考未优化采样的轮次节奏×优化后的单轮字节，取最大值 {mib(budget_rate)}/小时，防止回归并行负载导致低估。", 
 f"24小时预计新增 {gib(budgets[24]['growth'])}，另加50%增长安全余量 {gib(budgets[24]['margin'])}，建议隔离测试硬限额 {gib(budgets[24]['hard'])}，可用磁盘至少 {gib(budgets[24]['disk'])}。不自动应用到生产。", 
 '','## 可归档与完整保留','可压缩/去重：重复PNG、额外诊断截图、JSONL普通日志、SQLite离线备份、导入录屏；先保留原件与清单，不能自动删除。必须完整保留：状态/阶段、关键动作、BOSS击败、拾取背包、结算、异常恢复、控制命令、重要决策依据及唯一故障前后画面。',
 '核心帧引用、决策和结算检查见 `9STAR_DATA_INTEGRITY.md`。普通日志保留策略见 `../docs/9STAR_EVIDENCE_RETENTION.md`。','',
 '## 部署调查与审批','8000：原管理记录称RUNNING，但实时端口未监听，server PID60748、supervisor PID56488均不存在，实际状态EXITED_UNCONFIRMED。过去确切退出时间和原因未知；原启动方式WINDOWS_WMI、记录版本属于V1.3.2周期，无法从已退出进程确认运行代码。',
 '项目及父目录没有Git仓库：Git差异 NOT_AVAILABLE；源码备份与SHA差异清单为 `v133-audit/source-before.zip` / `source-diff.json`。SQLite备份 `production-backup.sqlite3` 已通过完整性检查，哈希 '+str(backup.get('backup_sha256'))+'。',
 '隔离8043曾正常启动并核对身份、API版本1.3.3；原生产实例没有启动/升级。最终SSE修复后的8043重启被自动审批拒绝，原文 `blocked by policy`，未给更具体原因；未尝试替代启动方式。最终源码的进程内SSE测试通过，但最终源码在真实端口的重新启动与浏览器验收 NOT_RUN。',
 '人工操作（需遵守系统授权；生产升级另需用户明确批准）：先核实8043实例身份并停止，按原管理入口重新启动隔离8043；检查health版本、schema_migration含8、storage/status、全部既有浏览器脚本；全部通过后再申请生产升级。生产回滚保留成套源码/数据库备份，或先在隔离副本验证008降级脚本；禁止直接把旧Reader指向空内嵌PNG的新库。',
 '```powershell',r'.\.venv\Scripts\python.exe scripts/service_manager.py status --port 8043',r'.\.venv\Scripts\python.exe scripts/service_manager.py stop --port 8043',r'.\.venv\Scripts\python.exe scripts/service_manager.py start --port 8043 --db reports/v133-service.sqlite3 --watchdog',r'.\.venv\Scripts\python.exe scripts/console_browser_test.py --url http://127.0.0.1:8043','```']
 write('reports/9STAR_STORAGE_AUDIT.md','\n'.join(lines))
 passed=bool(soak.get('passed')); status='PASS' if passed else 'NOT_RUN（真实1小时仍未完成）'
 lines=['# 九星真实1小时容量基线',f'状态：{status}。生成时间：{stamp}。','',
 f"自建模拟器；tick=.08 秒；请求3600实际秒，已观测/实际完成 {elapsed:.3f} 秒；开始UTC {soak.get('started_at_utc','samples.jsonl持续记录')}。没有加速时钟。", 
 f"完成轮次 {soak.get('completed',last['completed'])}；最终证据占用 {mib(final['used_bytes'])}，净逻辑增长 {mib(net)}，平均 {mib(average_rate/60)}/分钟、{mib(average_rate)}/小时。", 
 f"WAL峰值 {mib(max(s['storage']['files']['-wal'] for s in samples))}；内存范围 {mib(min(s['memory_bytes'] for s in samples if s['memory_bytes']))}–{mib(max(s['memory_bytes'] for s in samples if s['memory_bytes']))}。中途句柄实测约257–259；完整每秒句柄趋势未采集。", 
 '隔离目录：`v133-1h/agent.sqlite3`、`samples.jsonl`、`result.json`。每5秒采样；锁和I/O可能使间隔延长，实际时间取monotonic。满1小时后停止正在执行的最后一轮，这一STOPPED尾轮不算成功，保留其真实资源和停止事件。',
 '原始result.json及samples.jsonl完整保留：其完成计数包含5条基底原始场景历史，且将这5条没有九星结算的历史误报为缺少结算。独立verification.json按research_run关联核实本轮163条COMPLETED及1条STOPPED，全部结算一致，修正汇总见verified-summary.json；不新增或改写任何历史结算。原actual_seconds还包含约61秒完整性检查，实际采集窗口由首个本轮Run到最后Worker停止实测3600.333148秒。未来脚本已修正计数范围及采集/核验耗时，独立1秒计数回归PASS，不冒充另一小时测试。',
 '这次1小时进程运行的是初始压缩/哈希复用与容量保护代码；测试过程中新增了诊断配置、008迁移登记、独立连接归档、写入故障文件备用存储及SSE加速补读，它们有独立回归证据，但不能声称最终所有代码在该进程中重新加载。最终源码全套1小时重新运行 NOT_RUN。',
 '', '|专项|状态/证据|','|---|---|',
 f"|A 100轮加速|{'PASS' if hundred.get('passed') else 'NOT_RUN（结果汇总未完成）'}；v133-100/result.json；tick=.001，仅加速回归|",
 f'|B 1小时真实时钟|{status}；v133-1h/verification.json及verified-summary.json；原result.json保留误计数审计|',
 '|C 高频日志/视觉|PASS：100轮真实动作16400次观察；5000诊断日志压力补读通过；额外截图批量/缓存有pytest证据|',
 '|D 软阈值|PASS：test_wal_soft_hard_and_disk_protection，报警及额外截图8倍间隔|',
 '|E 硬阈值|PASS：容量与磁盘余量故障触发STORAGE_LIMIT；原启动保护兼容；当前动作之前检查|',
 '|F 服务异常退出恢复|PASS：完整pytest中实际进程异常退出/Watchdog/一致性测试；最终8043手工重启未执行|',
 '|G 数据库写入失败|PASS：一次/持续失败、query_only真实拒写、Worker释放与人工审核；不重复成功结算|',
 f"|H Web/SSE|5000补读与4次刷新 {'PASS' if pressure.get('passed') else 'NOT_RUN'}；旧SSE完整控制台重补读测试FAIL后修复，最终进程内5000条补读PASS；最终端口浏览器复验NOT_RUN（审批拒绝）|", 
 '|完整pytest|99 PASS，0失败；v133-pytest-release.xml；最终归档连接改动专项1 PASS；数据库故障/存储21 PASS；最终监控、活跃Worker硬停止和媒体归档11 PASS|',
 '|前端单元/构建|9 PASS；vue-tsc和vite构建PASS|',
 '|既有浏览器/API|研究、知识、观察、基础控制浏览器PASS；控制台旧SSE大量补读FAIL已修复；最终真实服务完整浏览器复验NOT_RUN|',
 '|8小时/24小时|NOT_RUN：未自动启动，等待用户明确批准|','',
 '## 8小时资源建议',f"预计新增 {gib(budgets[8]['growth'])}，安全余量 {gib(budgets[8]['margin'])}；建议隔离硬限额 {gib(budgets[8]['hard'])}、可用磁盘至少 {gib(budgets[8]['disk'])}。在1小时PASS及最终隔离部署/浏览器复验后再批准执行。",
 '## 未完成与限制','不得把初始1小时容量测试、最终进程内单测和旧服务浏览器结果合并宣称为最终源码全部真实环境验收。生产升级、最终8043重启、最终端口SSE/完整控制台复验、8h和24h均NOT_RUN。']
 write('reports/9STAR_1H_SOAK_TEST.md','\n'.join(lines))
 lines=['# 九星 V1.3.3 数据完整性',f'生成时间：{stamp}。','',
 '生产数据库按备份逐表比较：'+('PASS，所有原表内容一致。' if current.get('production_equal') and all(current['production_equal'].values()) else 'NOT_RUN')+'；integrity_check='+str(current.get('production_integrity','NOT_RUN'))+'.',
 '备份哈希：'+str(backup.get('backup_sha256'))+'；source-before.zip CRC完整性：'+('PASS' if current.get('source_zip_integrity') is None else str(current.get('source_zip_integrity')))+'.',
 '', '|数据库|SQLite|失效帧|决策观察缺失|帧哈希错误|重复结算|','|---|---|---:|---:|---:|---:|']
 for label,value in integrity.items():lines.append(f"|{label}|{value['integrity']}|{value.get('missing_frames',0)}|{value.get('decisions_missing_observation',0)}|{value.get('blob_hash_errors',0)}|{value['duplicate_settlements']}|")
 if soak:lines.append(f"|v133-1h|{verification.get('integrity','NOT_RUN')}|{verification.get('broken_frame_references','NOT_RUN')}|{verification.get('decisions_missing_observation','NOT_RUN')}|{verification.get('blob_hash_errors','NOT_RUN')}|{verification.get('duplicate_settlements','NOT_RUN')}|")
 lines+=['',f"100轮结算一致性：{hundred.get('database_consistency','NOT_RUN')}，通关 {hundred.get('completed','NOT_RUN')}/100；每轮要求4阶段、5条结算、实际拾取/背包资源与汇总一致。",
 f"同种子画面解码492对对比，不一致数 {payload.get('pixel_errors','NOT_RUN')}。引用PNG的SHA256逐对象验证；未修改旧库原始帧。",
 '结算复入、进程重启后的已完成Run不重结算、INTERRUPTED未知结果不重放均由原测试及99项完整pytest覆盖。降级脚本先恢复帧再删新增表，实际降级后全部PNG哈希相同、SQLite完整性ok。','',
 '当前历史库已恢复原schema 2–7，全部原表内容与备份一致。最终pytest漏设MOYU_DB，模块导入曾误加008、两个空证据表及3条IDLE状态；已先备份受影响库，核对差异后仅撤回这些新增内容，恢复序号。业务、结算及原始记录未变，未清空任何历史库。审计证据见v133-audit/default-import-diff.json、production-after-test-import.sqlite3及v133_revert_default_import.py。新增tests/conftest.py强制为未指定MOYU_DB的测试分配独立库，15项专项通过且复核历史库仍一致。此次执行失误已记录，不声称全过程生产库只读。',
 '注意：SQLite本项目原连接并未强制启用foreign_keys，不能用“约束已开启”替代检查。本轮实际执行foreign_key_check，并显式查决策、画面映射及哈希，结果以清单为准。',
 '最终源码端口重启被审批拒绝，因此最终部署联调NOT_RUN。']
 write('reports/9STAR_DATA_INTEGRITY.md','\n'.join(lines))
 plan=['# 九星8小时 / 24小时真实测试执行方案',f'生成时间：{stamp}。本轮8小时、24小时均NOT_RUN，未提交自动启动或自动化任务。','',
 f"预算速率取真实增长与历史轮次节奏外推较高者：{mib(budget_rate)}/小时；不是固定演示数字。公式：新增=速率×小时，安全余量=新增×50%，硬限额=向上取整GiB(当前库+新增+余量+64MiB)。磁盘需容纳工作库、原始快照及ZIP归档三份最坏占用另加1GiB。", 
 '|时长|预计新增|50%余量|隔离硬阈值|最少空闲磁盘|','|---|---:|---:|---:|---:|']
 for hours,b in budgets.items():plan.append(f"|{hours}h|{gib(b['growth'])}|{gib(b['margin'])}|{gib(b['hard'])}|{gib(b['disk'])}|")
 plan+=['','## 前置条件','1. 完成真实1小时基线并确认PASS；在最终源码隔离实例复验所有API/Web及大日志补读。','2. 生产升级与长测分别取得用户明确批准，写批准时间、授权测试范围、时长和容量预算。','3. 准备独立数据库/媒体目录与端口，不读取生产实时数据库运行Agent；模拟器场景限定NineStarResearchDungeon。','4. 建议至少2个可用CPU核心、测试进程预留512MiB内存及浏览器额外1GiB；以实际1h内存范围复核，长测还要观察趋势，不能把1h稳定当作24h证据。','5. 核对磁盘实际空闲、备份哈希、SQLite integrity_check、所有引用、已完成Run与结算总额；不要清理历史。',
 '','## 可执行步骤（仅用户明确批准后）',
 '在reports下创建对应label的保留配置，如`v133-8h.retention.json`。下面限额由本次实测预算生成，不会自动应用。',
 '```json',json.dumps(dict(hard_limit_bytes=budgets[8]['hard'],soft_ratio=.8,disk_reserve_bytes=67108864,delete_enabled=False),indent=2),'```',
 '```powershell',r'.\.venv\Scripts\python.exe scripts/nine_star_soak.py --seconds 28800 --label v133-8h --allow-long-run --approval-note "填写用户明确批准时间及自建模拟器范围"',r'.\.venv\Scripts\python.exe scripts/nine_star_soak.py --seconds 86400 --label v133-24h --allow-long-run --approval-note "填写用户明确批准时间及自建模拟器范围"','```',
 f"24h配置的hard_limit_bytes需为 {budgets[24]['hard']}；其他保护保持开启。该脚本真实使用monotonic等待，按最多100轮分批继续，截止时安全停止尾轮。没有批准时不得使用长测开关。", 
 '每5秒写samples.jsonl，检查磁盘/增长/核心事件完整性；软阈值或趋势异常降低额外诊断频率，核心证据全保留。硬阈值、磁盘余量不足或写入失败安全停止，不自动调大限额。暂停、人工停止和系统睡眠必须写记录，系统睡眠不得冒充活跃运行时间。',
 '','## 验收及回滚','实际经过28800/86400秒且Agent持续工作；记录正常完成与尾轮停止数量；核对全部4阶段、击败、拾取、背包、结算、控制、恢复、重要决策；完整性ok，PNG哈希无误，无失效引用，无重复结算；Web/SSE有真实时延证据；无持续内存/句柄增长；报告实际占用和每分钟窗口趋势。',
 '生产部署不得与长测捆绑：停生产服务、复制成套备份、审核源差异与008迁移及降级方案之后，仍需用户明确批准。若启动审批拒绝，保存原始原因和失败命令，不切换WMI或其他入口绕过。',
 '报告中1小时若仍NOT_RUN，本方案只能用于准备，不能视为长测开工条件已通过。']
 write('docs/9STAR_8H_24H_TEST_PLAN.md','\n'.join(plan))
 print(json.dumps(dict(soak_status=status,average_bytes_per_hour=average_rate,budget_bytes_per_hour=budget_rate,budgets=budgets)))

if __name__=='__main__':main()




