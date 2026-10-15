# 魔域 Web Agent V1.3.2

新增Windows托管启停、可选Watchdog、持久化服务心跳与Agent恢复审计。默认不自动拉起，不安装系统服务。快速启动 `start.ps1 -SkipSetup -NoBrowser`，用 `stop.ps1` 优雅停止；只有显式 `-Watchdog` 才启用自动恢复。见 [V1.3.2说明](docs/V1.3.2.md) 和 [实测报告](reports/V1.3.2_REPORT.md)。

# 魔域 Web Agent V1.3.1

控制台已修复连接失败恢复、Worker状态确认与缺失数值显示。API和SSE状态分开显示；未获取字段显示“待获取”，无有效样本显示“暂无有效数据”。见 [V1.3.1实测报告](reports/V1.3.1_REPORT.md)。服务启动方式仍使用下面的start.ps1或uvicorn；页面断线不会停止后台任务。

# 魔域 Web Agent V1.3

九星研究副本现可在自建模拟器自主规划、寻路、战斗、拾取、验证通关、独立结算并连续运行。入口 http://127.0.0.1:8000/#/research 。历史名称与模拟假设严格区分；原副本与V1.2观察页面保留。见 [V1.3说明](docs/V1.3.md) 和 [实测报告](reports/V1.3_REPORT.md)。

# 魔域 Web Agent V1.2

已增量加入授权画面导入、本地OCR、人工审核、九星标注与独立数字孪生研究配置。见 [V1.2说明](docs/V1.2.md) 与 [验收报告](reports/V1.2_REPORT.md)。生产库的未知值与历史事实保持独立。

# 魔域 Web Agent V1.1

V1.1 增量加入可追溯的阿拉玛官网知识库。运行环境仍为 SIMULATOR，官网事实为只读 OFFICIAL_WEB，当前官服有效性未验证。新增页面、采集、迁移、数据边界与 V1.2 建议见 [V1.1 说明](docs/V1.1.md)。以下保留 V1.0 运行与架构说明。

# 魔域智能游戏 Agent V1.0

Windows 本地原创二维副本实验系统。Vue3 + TypeScript + Vite 控制台，FastAPI 后端，SQLite 持久化，OpenCV 图像识别与 A* 决策，pytest / Playwright 验收。

**所有地图、怪物、资源和收益都是 SIMULATED。仅控制自建模拟器，不接入正式游戏，不包含注入、协议修改或反作弊绕过。**

## Windows 一键运行

需要 Python 3.11 或更新版本、Node.js 20.19+ 或 22.12+、npm，以及首次下载依赖所需网络。本工作区实际使用 Python 3.14.3 / Node.js 24.14.1 验证。

```powershell
cd D:\myAI\moyu-v1\moyu-agent
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

打开 http://127.0.0.1:8000 ，选择轮数与种子，点击“启动 Agent”。默认两轮；后台自行观察、移动、攻击、拾取、验证四阶段、走到出口、结算并创建下一轮。Ctrl+C 退出服务。`start.ps1 -NoBrowser` 只启动服务。首次构建需要下载依赖，后端启动前打开的浏览器可能需要刷新。

日常快速启动（依赖与前端已构建）：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

后端同时托管构建后的 Vue 页面，前后端同源。开发时另开终端执行 `cd frontend; npm run dev`，访问 http://127.0.0.1:5173 ，Vite 代理 `/api` 到 8000。

## 测试与证据

```powershell
# 自动启动独立 8001 测试服务、使用独立 SQLite、运行单元/集成/浏览器验收并生成报告
powershell -ExecutionPolicy Bypass -File .\test.ps1
# 仅单元和集成测试
powershell -ExecutionPolicy Bypass -File .\test.ps1 -SkipBrowser
# 对正在运行的 8000 服务执行真实浏览器验收；会实际创建任务及奖励记录
.\.venv\Scripts\python.exe scripts/browser_test.py --url http://127.0.0.1:8000
.\.venv\Scripts\python.exe scripts/report.py
```

浏览器测试包括两轮通关、暂停/继续、刷新、阻塞恢复、异常失败、停止与紧急停止、数据库与导出一致性、SSE Last-Event-ID 补读。pytest 还检查三个种子的视觉识别真值、A* 障碍、资源事件总和、有限重试、动作/副本超时和服务重启中断标记。

- `reports/TEST_REPORT.md`：自动生成的实际结果与未通过项。
- `reports/pytest.xml`：JUnit 单元/集成报告。
- `reports/browser-results.json`：逐项浏览器验收结果。
- `reports/actual-events.jsonl`：实测服务导出的真实运行事件。
- `reports/console-running.png` / `console-final.png`：实际页面截图。
- `reports/test-server*.log`：自动测试服务日志。
- `data/agent.sqlite3`：日常运行数据库。已有验收记录来自实际 Agent 行动，未预置完成任务。

## 实现结构

```text
backend/world.py     原创 20×14 格世界、四阶段首领与护卫、障碍、入口出口、随机奖励、故障注入、独立渲染
backend/agent.py     图像观察 / 决策 / 动作 / 验证接口、OpenCV 分割、A*、有限重试
backend/runtime.py   独立后台 Worker、状态机、动作锁、暂停屏障、停止、超时、真实结算
backend/store.py     SQLite 建表与索引、WAL、事件序列、启动中断恢复、收益聚合
backend/main.py      REST / SSE / PNG / 本机访问防护 / 静态前端
frontend/src/        中文深色控制台、画面、控制、任务、收益、日志筛选与事件回放
scripts/init_db.py   幂等初始化数据库（正常启动也会自动初始化）
```

Agent 状态包含 `IDLE、PREPARE、ENTER、SEARCH、NAVIGATE、COMBAT、LOOT、VERIFY、SETTLEMENT、RECOVER、STOPPED`。暂停是运行控制标识，保留暂停前动作状态。每轮创建新的实际世界；每阶段 1 名首领（75 HP）与 1 名护卫（50 HP），角色攻击 25，攻击会收到 2 点反击。掉落由种子确定，只在角色到达物品格并执行拾取后增加背包。四阶段击败且拾取清空后出口开放，视觉识别出口、寻路退出后才写入成功结算。

**感知边界**：`Vision.observe` 只接受 NumPy 图像；配色分割与连通区域提取位置，图像顶部像素 HUD 提供阶段/生命/进入/完成/退出标志。运行感知不接收世界对象或内部坐标。Worker 作为环境适配器持有世界，用渲染图像供 Agent 观察，用动作接口执行 Agent 指令；Web 状态可读取环境真值用于展示。只有测试评估器直接对比真值。固定配色识别是 V1 模拟环境基线，未宣称适用于正式客户端。

Worker 与 SSE 浏览器连接完全分离。浏览器关闭不会停止 Worker；刷新查询真实状态与数据库历史。移动、攻击、拾取都修改世界状态并记录事件。UI 的 400ms 轮询只更新展示，Worker 的 tick 只限制实际动作频率，不能推进地图阶段或生成奖励。暂停成功响应前获取动作锁，之后不再执行新动作。停止等待 Worker 退出，保留最后画面供查看。

动作预算 2 秒、单轮活动预算 180 秒、最多 1000 次动作、连续验证失败最多重试 3 次。暂停时间不计入单轮活动超时，历史耗时使用真实墙钟时间。异常写入 FAILED；主动停止写入 STOPPED；服务下次启动将遗留 RUNNING 标记为 INTERRUPTED。成功率不预置、不伪造；平均通关时间与累计奖励只统计 COMPLETED 记录，失败数包括 FAILED 和 INTERRUPTED。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康与数据源 |
| GET | `/api/agent/status` | 实际运行状态 |
| POST | `/api/agent/start` | `{rounds:1..100, seed:整数, fault:null/"stuck"/"crash"}` |
| POST | `/api/agent/pause`、`resume`、`stop`、`emergency-stop` | 校验状态，不合法返回 409 |
| GET | `/api/runs`、`/api/runs/{id}` | 任务与全部事件 |
| GET | `/api/logs` | `after/runId/level/search/limit` 筛选，按全局 id 分页 |
| GET | `/api/logs/stream` | SSE，支持 `Last-Event-ID` 与 `after` |
| GET | `/api/logs/export` | JSONL；可指定 `runId` |
| GET | `/api/frame/latest` | 实际渲染 PNG，无缓存 |
| GET | `/api/inventory`、`earnings`、`dungeon/status` | 背包、成功结算累计、世界状态 |
| GET | `/api/knowledge/{kind}` | 官网事实预留查询 |

每条事件包含 `id/timestamp/sequence/runId/stage/level/eventType/message/details`；timestamp 为 UTC ISO 8601，页面按浏览器时区显示。id 是全局 SSE 游标，sequence 是单任务顺序号。不会记录凭据。

控制请求须提供 `X-Agent-Control: local-console`，同时检查同源 Origin、本机客户端与允许的 Host。默认只绑定 127.0.0.1。这个请求头是本地控制校验，不是公网登录认证；不得通过反向代理或修改监听地址将本服务暴露公网。公网部署需要另行实现身份认证、权限与 TLS，本版本不支持。

数据库通过 `MOYU_DB` 环境变量可设置到独立文件。只能运行一个 Uvicorn worker；禁止多进程 workers 和 reload 模式同时操作同一运行库。SQLite 初始化包含三张表：runs、logs、knowledge，幂等创建及索引，无外部数据库需求。

## 官网知识隔离与未实施范围

知识实体预留 `map/monster/boss/dungeon/item/quest/source/game_version`，单独使用 knowledge 表、source 和 game_version 字段，返回 `OFFICIAL_UNCOLLECTED`。首版本没有采集官网事实，不向知识表写模拟地图、怪物或掉落率。

事件回放重放真实数据库事件顺序，不包含历史画面录像。当前地图布局固定，随机种子控制掉落。使用规则视觉与 A*，未训练通用图像模型。当前同步动作是有界纯 Python 世界更新；动作超时在执行返回时检查，未来接入外部阻塞控制器必须改成可取消的进程适配器。没有实现正式客户端或其他测试环境适配器。
# GitHub 发布快照说明

本仓库首次发布包含当前项目源码及 V1.3.4-P2/P3 报告。当前状态仍为 **ROOT_CAUSE_UNCONFIRMED / 8H_BLOCKED**，不是正式验收通过或生产发布。

生产数据、本地环境、测试数据库、媒体和运行证据保留在本地，不随公开仓库上传。报告中引用的本地证据路径需要原工作区；部分研究测试依赖本地 `reports/v13-baseline/agent.sqlite3` 基线，克隆仓库后不能在缺少该受验证基线的情况下直接复现全量验收。不得改用生产数据库补齐测试基线。

