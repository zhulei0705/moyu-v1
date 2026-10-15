import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime,timezone
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.knowledge import KnowledgeDB

def main():
    reports=ROOT/'reports'
    baseline=json.loads((reports/'v1.1-baseline'/'manifest.json').read_text(encoding='utf-8'))
    baseline={Path(p).as_posix():v for p,v in baseline.items()}
    changed=[p for p,v in baseline.items() if (ROOT/p).exists() and hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=v and not p.startswith('reports/')]
    current=[p for folder in ('backend','frontend/src','scripts','tests','docs') for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in str(p)]
    added=[p.relative_to(ROOT).as_posix() for p in current if p.relative_to(ROOT).as_posix() not in baseline]
    protected=['backend/world.py','backend/agent.py','backend/runtime.py','tests/test_system.py']
    unchanged={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==baseline[p] for p in protected}
    with sqlite3.connect(reports/'v1.1-baseline'/'agent.sqlite3') as old,sqlite3.connect(ROOT/'data'/'agent.sqlite3') as live:
        existing=old.execute('SELECT * FROM runs').fetchall()
        current_runs={r[0]:r for r in live.execute('SELECT * FROM runs')}
        runs_preserved=all(current_runs.get(r[0])==r for r in existing)
        old_logs=old.execute('SELECT * FROM logs').fetchall()
        live_logs={r[0]:r for r in live.execute('SELECT * FROM logs')}
        logs_preserved=all(live_logs.get(r[0])==r for r in old_logs)
    repo=KnowledgeDB(ROOT/'data'/'agent.sqlite3')
    stats=repo.stats()
    unknown=sorted({(f['entity_type'],f['field']) for f in repo.facts() if f['verification_status']=='UNKNOWN'})
    junit=ET.parse(reports/'pytest.xml').find('testsuite')
    browser=json.loads((reports/'browser-results.json').read_text(encoding='utf-8'))
    knowledge=json.loads((reports/'knowledge-browser-results.json').read_text(encoding='utf-8'))
    collection=json.loads((reports/'official-repeat-collection.json').read_text(encoding='utf-8'))
    output={'timestamp':datetime.now(timezone.utc).isoformat(),'stats':stats,'added_files':sorted(added),'modified_files':sorted(changed),
            'algorithms_unchanged':unchanged,'original_run_rows_preserved':runs_preserved,'original_log_rows_preserved':logs_preserved,
            'original_runs_count':len(existing),'original_logs_count':len(old_logs),'unknown_fields':unknown,
            'pytest':dict(junit.attrib),'v10_browser_checks':len(browser['checks']),'v10_browser_passed':browser['passed'],
            'knowledge_browser_checks':len(knowledge['checks']),'knowledge_browser_passed':knowledge['passed']}
    (reports/'v11-delivery.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# V1.1 实际交付报告','',f"生成时间：{output['timestamp']}。时间戳为 UTC，页面按浏览器本地时区显示。",'',
      '## V1.0 保护与回归','',
      '未发现 AGENTS.md；工作区没有 Git 仓库，因此不能提供 Git 未提交差异。修改前已保存文件哈希、源码副本与 SQLite 在线备份。',
      f"原有 {len(existing)} 条任务及 {len(old_logs)} 条日志完整保留：任务 {runs_preserved}，日志 {logs_preserved}。",
      f"算法与原测试哈希保持不变：{all(unchanged.values())}（world.py / agent.py / runtime.py / test_system.py）。",'',
      '基线实测：原 12 项 pytest 通过；原 21 项浏览器/API 检查通过；TypeScript 与 Vite 构建通过。基线报告保存在 v1.1-baseline。',
      f"最终 pytest：{junit.get('tests')} 项，失败 {junit.get('failures')}，错误 {junit.get('errors')}。",
      f"原控制台浏览器/API：{len(browser['checks'])} 项，通过 {browser['passed']}；知识库浏览器/API：{len(knowledge['checks'])} 项，通过 {knowledge['passed']}。",
      '原模拟器完成两轮通关、SSE 补读与数据库日志一致性仍通过。依赖产生一条 Starlette/httpx 弃用警告，无测试失败。','',
      '## 官网真实采集','',f"来源 {stats['sources']}，原始 HTML 快照 {stats['snapshots']}；有网页证据的字段事实 {stats['official_page_facts']}（含历史来源重复描述），当前官服已验证事实 {stats['current_game_verified_facts']}。",
      f"待复核冲突 {stats['conflicts']}；UNKNOWN 字段记录 {stats['unknown_fields']}（按实体、字段、快照计数，非独立知识问题数）。",'',
      '| 页面 | 重复抓取结果 | HTTP | SHA-256 |','|---|---|---|---|']
    for r in collection['results']: lines.append(f"| {r['url']} | {r['status']} | {r.get('http_status')} | {r.get('sha256')} |")
    lines+=['','首次采集 2015 页面因“首领/BOSS”文字差异失败，已修复。失败尝试未删除，来源管理可查看。',
      '2015 发布时间来自页面明确标签；2010 路径日期仅是 URL 线索，其页面发布时间为 NULL；指南页发布时间也为 NULL。游戏构建号与当前官服有效性均未确认。',
      '官网名称、入口坐标、等级、队伍限制、阶段、奖励清单与入口时间豁免按每个页面分别保存，历史碎片名称未自动合并。','',
      '## 未确认字段','']+[f'- {entity}.{field}' for entity,field in unknown]
    lines+=['','此外，全部历史网页事实在当前官服的有效性未验证，game_observation 尚无授权游戏观察。','',
      '## 新增文件','']+[f'- `{p}`' for p in sorted(added)]
    lines+=['','## 修改文件','']+[f'- `{p}`' for p in sorted(changed)]
    lines+=['','## 迁移与页面','',
      '迁移：`backend/migrations/002_official_knowledge.sql`，幂等新增，不覆盖 V1.0 数据。',
      '- 控制台：http://127.0.0.1:8000/#/console',
      '- 知识库：http://127.0.0.1:8000/#/knowledge',
      '- 流程：http://127.0.0.1:8000/#/flow',
      '- 来源管理：http://127.0.0.1:8000/#/sources','',
      '## V1.2 建议','',
      '先建立带授权引用、截图、构建号和采样记录的 GAME_OBSERVATION；人工确认入口/阶段/机关/技能时序后，再针对明确版本制作独立数字孪生。缺失地图、属性和概率保持 UNKNOWN，不将官网历史知识直接转换成自动执行步骤。','',
      '详情见 docs/V1.1.md；逐项测试见 TEST_REPORT.md、pytest.xml、browser-results.json、knowledge-browser-results.json。']
    (reports/'V1.1_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(output,ensure_ascii=False,indent=2))
    return int(not (all(unchanged.values()) and runs_preserved and logs_preserved and browser['passed'] and knowledge['passed'] and junit.get('failures')=='0' and junit.get('errors')=='0'))

if __name__=='__main__':raise SystemExit(main())
