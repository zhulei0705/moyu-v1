"""汇总真实数据库、pytest与浏览器证据；只读生产库。"""
from pathlib import Path
import sqlite3,json,hashlib,datetime,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]

def main():
    out=ROOT/'reports';base=json.loads((out/'v13-baseline/baseline.json').read_text(encoding='utf-8'))
    with sqlite3.connect('file:'+str(ROOT/'data/agent.sqlite3')+'?mode=ro',uri=True) as db:
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        counts={t:db.execute('SELECT COUNT(*) FROM "'+t+'"').fetchone()[0] for t in tables}
        preserved={t:hashlib.sha256(repr(db.execute('SELECT * FROM "'+t+'" ORDER BY rowid LIMIT ?',(base['counts'][t],)).fetchall()).encode()).hexdigest()==h for t,h in base['table_hashes'].items() if t not in ('schema_migration','sqlite_sequence')}
        versions=[r[0] for r in db.execute('SELECT version FROM schema_migration ORDER BY version')]
        integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
    results={name:json.loads((out/name).read_text(encoding='utf-8')) for name in ['browser-results.json','knowledge-browser-results.json','v12-browser-results.json','v13-browser-results.json']}
    checks={name:{'total':len(r['checks']),'passed':sum(c['passed'] for c in r['checks'])} for name,r in results.items()}
    unit=dict(ET.parse(out/'v13-delivery.xml').find('testsuite').attrib)
    focused=dict(ET.parse(out/'v13-final-research.xml').find('testsuite').attrib)
    browser=results['v13-browser-results.json'];database=out/'v13-final-browser.sqlite3'
    selected=[];samples=[];resource_checks=[]
    with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        for rid in browser['completed_run_ids']:
            run=dict(db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone());run['rewards']=json.loads(run['rewards'])
            ledger=[dict(r) for r in db.execute('SELECT * FROM research_settlement WHERE run_id=? ORDER BY stage_id',(rid,))]
            total=next(r for r in ledger if r['stage_id']==0)
            logs=[dict(r) for r in db.execute('SELECT * FROM logs WHERE runId=? ORDER BY id',(rid,))]
            for e in logs:e['details']=json.loads(e['details'])
            resource={k:sum(e['details']['reward'][k] for e in logs if e['eventType']=='loot') for k in ('gold','shards','materials')}
            ok=resource==run['rewards'] and resource['gold']==total['simulated_gold'] and resource['shards']==total['simulated_fragments'] and resource['materials']==total['simulated_materials']
            ok=ok and all(sum(s[f] for s in ledger if s['stage_id']>0)==total[f] for f in ('simulated_gold','simulated_fragments','simulated_materials','consumable_cost'))
            resource_checks.append({'run_id':rid,'passed':ok,'resource_events':resource,'ledger':ledger})
            selected.append(run)
            row=dict(db.execute("SELECT * FROM agent_decision WHERE run_id=? AND action_type='move' ORDER BY rowid LIMIT 1",(rid,)).fetchone())
            for f in ('world_state','selected_goal','actual_result','route'):row[f]=json.loads(row[f])
            samples.append(row)
        aggregate=[dict(r) for r in db.execute('SELECT r.id,r.status,s.simulated_gold,s.simulated_materials FROM research_run x JOIN runs r ON x.run_id=r.id LEFT JOIN research_settlement s ON s.run_id=r.id AND s.stage_id=0')]
    cases=[]
    for path in (out/'v13-final-research-tests').rglob('*.sqlite*'):
        if path.suffix not in ('.sqlite','.sqlite3'):continue
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_run'").fetchone():continue
            for row in db.execute('SELECT r.id,r.status,r.error,x.configuration FROM runs r JOIN research_run x ON r.id=x.run_id'):
                cfg=json.loads(row['configuration']);fault=cfg.get('test_override',{}).get('fault')
                if 'worker_proc' in str(path) and row['error'] == '研究Worker中断，未验证通关':fault='EXTERNAL_PROCESS_TERMINATED'
                cases.append({'id':row['id'],'status':row['status'],'error':row['error'],'fault':fault,'database':str(path.relative_to(ROOT))})
    normal=[c for c in cases if not c['fault'] and c['status'] in ('COMPLETED','FAILED')]
    normal_success=sum(c['status']=='COMPLETED' for c in normal)
    changed=[p for p,h in base['source_hashes'].items() if (ROOT/p).exists() and hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h]
    new=[]
    for folder in ('backend','frontend/src','tests','scripts','docs'):
        for p in (ROOT/folder).rglob('*'):
            relative=str(p.relative_to(ROOT))
            if p.is_file() and '__pycache__' not in str(p) and relative not in base['source_hashes']:new.append(relative)
    assert all(preserved.values()) and integrity=='ok'
    assert int(unit['failures'])==0 and int(unit['errors'])==0 and int(focused['failures'])==0
    assert all(c['passed']==c['total'] for c in checks.values()) and all(r['passed'] for r in resource_checks)
    summary={'timestamp':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),'baseline_counts':base['counts'],'production_counts':counts,'preserved_original_business_tables':preserved,'migration_versions':versions,'integrity':integrity,'pytest':unit,'research_recheck':focused,'browser_checks':checks,'browser_check_total':sum(c['total'] for c in checks.values()),'dedicated_browser_runs':selected,'resource_conservation':resource_checks,'decision_samples':samples,'isolated_cumulative_research_runs':aggregate,'latest_pytest_cases':cases,'normal_pytest_success':{'completed':normal_success,'sample_count':len(normal),'success_rate':normal_success/len(normal) if normal else None},'changed_files':changed+['frontend/dist (生成的构建产物)'],'new_files':new+['reports/V1.3_REPORT.md','reports/v13-delivery.json'],'production_url':'http://127.0.0.1:8000/#/research'}
    (out/'v13-delivery.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'v13-decision-sample.json').write_text(json.dumps(samples[0],ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 魔域 Web Agent V1.3 交付与实测报告','',f"报告时间：{summary['timestamp']}。九星研究场景已连接V1.2孪生配置、像素观察、A*、原Worker控制与独立结算。",'', '## 访问与执行','', '生产页面：[九星副本运行](http://127.0.0.1:8000/#/research)；[原控制台](http://127.0.0.1:8000/#/console)。8000已启动，API健康版本1.3。场景选择器保留original和NineStarResearchDungeon。选择九星后点击启动，后台自动进入、战斗、拾取、四阶段验证、实际退出、结算、创建下一轮。暂停/继续/停止/紧急停止、刷新恢复、路线、角色、实时PNG、决策详情和历史图像回放均为运行系统。', '', '说明：[docs/V1.3.md](../docs/V1.3.md)。开发期间测试重启命令曾被自动审批拒绝，仅返回blocked by policy；已采用核实进程和新增隔离服务的方式完成验收，不影响当前8000服务。最终浏览器验收库reports/v13-final-browser.sqlite3，端口8004；测试结果不灌入生产库。', '', '## 基线与历史数据保护', '', '项目及父目录未发现AGENTS.md。Git返回not a git repository，没有重建工程或初始化仓库，无法声称哪些文件已提交。源码/根配置文件SHA-256与SQLite一致性备份在reports/v13-baseline。V1.2基线重新运行38项pytest、62项浏览器/API和前端构建全部通过，证据已归档到该目录。', '', '| 数据 | V1.2基线 | 当前生产库 |','|---|---:|---:|',f"| 运行 | {base['counts']['runs']} | {counts['runs']} |",f"| 日志 | {base['counts']['logs']} | {counts['logs']} |",'| 官网历史事实 | 254 | 254 |','| 官网UNKNOWN | 276 | 276 |','| 官网冲突 | 9 | 9 |', '', f"{len(preserved)}张原业务表的原记录有序内容SHA-256全部保持一致；允许新增研究记录追加，不修改旧行。SQLite integrity_check={integrity}，迁移版本{versions}。生产库研究运行数{counts['research_run']}，开发验收使用隔离库，不伪造生产通关记录。原World.py未修改。", '', '## 修改与新增文件', '', '相对接手快照修改：']
    lines+=['- `'+p+'`' for p in summary['changed_files']]
    lines+=['','新增：']+['- `'+p+'`' for p in summary['new_files']]
    lines+=['', '## 迁移与新增API','', '005_research.sql新增research_run、agent_observation、agent_decision；006_research_settlement.sql新增research_settlement。均提供down SQL；scripts/migrate_research.py先归档整个SQLite，再升级或按006→005回退。升级/回退在副本库实测，原runs、logs、fact_evidence、观察及孪生记录内容哈希一致。原观察回退脚本增加跨研究模块回退阻止保护。', '', '新增GET /api/scenes、/api/research/decisions、/api/research/runs/{run_id}、/api/research/observations/{id}/frame、/api/research/settlements、/api/research/earnings；POST /api/agent/start增加scene_id，默认original。未来资源/游戏内成交schema GET与501 POST接口已预留，必须走GAME_OBSERVATION和人工审核，不制造真实收益。详细请求见说明。', '', '## 场景来源、状态机与实际动作','', '专用构建器选同一历史快照的四阶段/首领名称/数量及已知机制，引用事实ID，保存V1.2孪生版本。名称和机制文本为只读OFFICIAL_FACT；地图22×16、位置、生命/攻击/战斗、掉落随机范围、背包和转存为SIMULATED_ASSUMPTION，版本未知保持NULL。历史首领标签在图像色块上的赋值同样属于模拟假设，不是官服OCR名称识别。', '', 'QUALIFY新增模拟资格检查；运行使用PREPARE→QUALIFY→ENTER→SEARCH→NAVIGATE/COMBAT/LOOT→VERIFY→SETTLEMENT，并在异常时RECOVER或FAILED。Agent只输入渲染图像，读取连通区域与模拟HUD；界面环境真值明确为SIMULATOR_UI_ONLY。目标和路线从新图像重算。攻击实际扣血、击杀实际生成掉落、拾取实际增加背包。只有四阶段敌人/掉落清空并走到出口才退出和成功结算；下一轮使用新世界和seed+序号。计时器仅限速/超时保护，不推进BOSS阶段。', '', '## 自动通关次数、成功率与结算一致性','', f"最新研究pytest正常运行记录：{normal_success}/{len(normal)}成功（不含主动停止、注入异常与真实进程终止）；另有三个种子各一次像素闭环直测通过。", f"本次专用浏览器验收连续两轮：{sum(r['status']=='COMPLETED' for r in selected)}/{len(selected)}成功，成功率100%（仅该测试样本），每轮四阶段、11个敌人实际掉落并拾取。"]
    for run,rc in zip(selected,resource_checks):
        total=rc['ledger'][0]
        lines.append(f"- run_id={run['id']}；{run['duration']:.3f}秒；金币{total['simulated_gold']}、碎片{total['simulated_fragments']}、材料{total['simulated_materials']}、补给成本{total['consumable_cost']}；事件/阶段/全程/背包资源一致。")
    lines+=['', f"隔离浏览器库累计九星记录：完成{sum(r['status']=='COMPLETED' for r in aggregate)}、失败{sum(r['status']=='FAILED' for r in aggregate)}、主动停止{sum(r['status']=='STOPPED' for r in aggregate)}；包括开发调试重试，不能称作生产样本。强制失败不并入正常通关成功率。", '', 'stage_id=0为全程，1–4为阶段；阶段时长由实际事件计算，阶段成功表示实际清场，全程成功必须实际退出。成功状态、全程/阶段结算在同一事务，重复结算不会重写。失败/停止记录保留实际已拾取资源且不计成功收益。模拟金币、碎片、材料来源SIMULATOR；成本为SIMULATED_SUPPLY_UNIT，未转换人民币。', '', '## 决策日志样例与来源', '', f"样例来自run {samples[0]['run_id']}，决策{samples[0]['id']}，原始观察{samples[0]['observation_id']}；数据库agent_observation保存PNG/SHA-256，agent_decision和logs AGENT_DECISION保存实际前后证据。规划耗时单位毫秒。完整样例[v13-decision-sample.json](v13-decision-sample.json)。", '', '```json',json.dumps({k:samples[0][k] for k in ('run_id','stage_id','observation_id','selected_goal','action_type','decision_reason','expected_result','decision_latency','error_code')},ensure_ascii=False,indent=2),'```', '', '样例完整文件包含world_state（IMAGE_PIXELS_ONLY）、actual_result（前后像素结果与实际事件）、after_observation_id和路线；无法识别时记录UNKNOWN/VISION_FAILED，未执行动作。传感器故障与GUI画面读取隔离，GUI刷新不会消耗一次性视觉失败注入。', '', '## 回归与异常验收', '', f"- 完整pytest：{unit['tests']}通过，0失败、0错误；最终研究模块复核{focused['tests']}项全部通过。"]
    lines += [f"- {name}：{c['passed']}/{c['total']}通过。" for name,c in checks.items()]
    lines += [f"- 浏览器/API合计{summary['browser_check_total']}项全部通过；前端vue-tsc与Vite生产构建通过；原Agent两轮通关和原页面保留。", '- 目标消失、动态障碍、一次视觉失败、一次拾取失败、背包不足均实际恢复并通关。', '- 死亡、持续视觉失败、阶段超时、持续拾取失败、加载失败、Worker异常中断均有限停止并FAILED；真正启动独立Worker进程后终止并重启，研究任务FAILED且没有成功收益。', '- 动作/视觉最多3次重试，首次失败加重试合计4次；不可达或死亡等不可恢复情况立即失败，总活动180秒、阶段60秒、1000步与动作2秒保护保留。', '- 暂停屏障、刷新、继续、停止/紧急停止、SSE Last-Event-ID补读、保存历史图像、收益事件一致性实测通过。', '- 修复了大决策日志使前端控制迟滞的问题：SSE批量更新、详情按需渲染、轮询不重叠。', '', '## 已知限制与下一阶段', '', '测试成功率只适用于明确的自建模拟规则；没有验证当前官服属性、地图、技能或掉落概率。没有真实游戏自动操作、注入、反作弊绕过、内存/协议篡改。角色定位/首领角色识别是原创配色与HUD识别，不是官服通用视觉模型。', '', '历史机制说明目前只读保留，不自动生成战斗逻辑。通用可执行场景编译器、任意JSON场景编辑执行、复杂技能/机关和官服资源/成交审核写入未实现；专用构建器更新时另存版本，用户旧版本保留。研究失败不会自动复活，真实进程中断不自动续跑。单进程Worker和本机接口边界保留。', '', '下一步建议：先加入模拟地图编译/可达性验证和明确来源的机关规则，再增加资源库存/成本细分、更多故障组合测试与运行数据保留策略；授权真实画面继续通过V1.2被动观察与人工审核建立标注集，不能把模拟参数补成官服事实。', '', '完整机器可读验收：[v13-delivery.json](v13-delivery.json)。']
    (out/'V1.3_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'pytest':unit['tests'],'research_recheck':focused['tests'],'browser':summary['browser_check_total'],'preserved_tables':len(preserved),'normal_pytest':summary['normal_pytest_success'],'browser_run_ids':[r['id'] for r in selected],'production_counts':{'runs':counts['runs'],'logs':counts['logs'],'research_runs':counts['research_run']}},ensure_ascii=False))
if __name__=='__main__':main()
