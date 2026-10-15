from pathlib import Path
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
root=Path(__file__).resolve().parents[1]/'reports'
lines=['# 实际自动测试报告','',f'生成时间：{datetime.now(timezone.utc).isoformat()}','', '所有游戏运行与奖励均为 SIMULATED。','']
if (root/'frontend-build-result.json').exists():
    build=json.loads((root/'frontend-build-result.json').read_text(encoding='utf-8-sig'))
    lines += [f"Vue TypeScript 检查与 Vite 构建：{'通过' if build['passed'] else '未通过'}；exit={build['exitCode']}。",'']
if (root/'pytest.xml').exists():
    suite=ET.parse(root/'pytest.xml').find('testsuite')
    lines += [f"pytest：{suite.get('tests')} 项，失败 {suite.get('failures')}，错误 {suite.get('errors')}，耗时 {suite.get('time')} 秒。",'']
    for case in suite.findall('testcase'):
        problem=case.find('failure')
        if problem is None: problem=case.find('error')
        lines.append(f"- {'通过' if problem is None else '未通过'}：{case.get('name')}")
        if problem is not None: lines.append(f"  {problem.get('message')}")
if (root/'browser-results.json').exists():
    report=json.loads((root/'browser-results.json').read_text(encoding='utf-8'))
    lines+=['',f"Playwright / HTTP 实测：{'通过' if report['passed'] else '存在失败'}；{report['timestamp']}。",'']
    for c in report['checks']: lines.append(f"- {'通过' if c['passed'] else '未通过'}：{c['name']}")
if (root/'knowledge-browser-results.json').exists():
    report=json.loads((root/'knowledge-browser-results.json').read_text(encoding='utf-8'))
    lines+=['',f"V1.1 知识库 Playwright / API 实测：{'通过' if report['passed'] else '存在失败'}；{report['timestamp']}。",'']
    for c in report['checks']: lines.append(f"- {'通过' if c['passed'] else '未通过'}：{c['name']}")
if (root/'official-collection.json').exists():
    report=json.loads((root/'official-collection.json').read_text(encoding='utf-8'))
    lines+=['','官网实际采集统计：',f"- 来源 {report['stats']['sources']}，快照 {report['stats']['snapshots']}。",
            f"- 有官网页面证据的字段事实 {report['stats']['official_page_facts']}；当前官服已验证 {report['stats']['current_game_verified_facts']}。",
            f"- 冲突 {report['stats']['conflicts']}，未检测到字段 {report['stats']['unknown_fields']}（按快照/实体/字段计数）。"]
    for row in report['results']: lines.append(f"- {row['url']}：{row['status']}；HTTP {row.get('http_status','UNKNOWN')}。")
lines+=['','范围限制：当前官服知识有效性未验证；仅验证原创固定配色模拟画面；事件回放不含历史图像录像。',
        '', 'V1.1 首次解析 2015 页面因“首领/BOSS”文字差异失败，已修复并保留真实失败记录。原始值与规范值分别存储，提取器升级保留旧解析结果归档。',
        '', 'V1.0 开发中曾发现角色遮挡掉落和 SQLite 同步刷盘延迟，均已修复并重新测试。首次 pytest 临时目录权限失败，已改为工作区内独立临时目录。']
(root/'TEST_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(root/'TEST_REPORT.md')
