# 九星8小时容量执行方案
数据依据：最终代码SHA256 `a2853686cf7f380aca975e003794c598c76f99c880fb892ebc36d6dbf2d4ce04`，真实1小时状态PASS；不使用先前修复快照替代。
当前证据占用 0.416 GiB；平均净增长 423.971 MiB/小时。为避免均值低估，取最高实测分钟外推 483.799 MiB/小时作为保守预算，另加50%增长余量和64MiB固定保留。

|时长|预计新增GiB|50%余量GiB|建议隔离硬限额GiB|最小可用磁盘GiB|
|---|---:|---:|---:|---:|
|8小时|3.780|1.890|7|22|
|24小时|11.339|5.670|18|55|

当前磁盘可用 530.306 GiB。默认硬限额仍2GiB、软阈值80%、最低磁盘保留64MiB，全部保护开启；默认2GiB不能承诺8小时。需按上述真实测量预算为新隔离长测配置限额，并取得用户明确批准，不自动调大生产容量。
归档实际释放量：0 bytes。实际ZIP 35403241 bytes，原件和验证快照保留；归档会额外占空间，不得把压缩率当作已释放容量。最小磁盘预算按工作证据、原始快照、ZIP三份最坏情况+1GiB其他空间计算。
8小时前置条件：最终1h、完整pytest、全部浏览器/API与历史哈希通过；磁盘达到预算；用户批准8小时与隔离容量配置；再次核对源码SHA不变、实例身份及备份。满足技术测试但未批准长测配置时只能标为“待批准”，不能宣称已经通过8小时。
本轮最终总验收状态PASS。8h运行NOT_RUN，24h运行NOT_RUN；不创建自动化、不定时启动。
获准后使用独立数据库和端口，记录源码SHA、环境、批准时间，沿用真实monotonic及CPU/内存/句柄/每分钟采样。若采用scripts/nine_star_soak.py，必须显式设置测试环境、根目录和MOYU_DB，且使用--allow-long-run及--approval-note；当前final_acceptance.py刻意拒绝大于3600秒，不得直接改时长绕过本轮范围。
归档/删除需清单、备份与引用完整性验证；本轮没有批准删除，释放预算始终按0。软阈值降低额外诊断采集，不丢核心事件；硬阈值/磁盘不足/写入失败安全停止。保留尾轮及失败证据，禁止重复结算。
如源码改变，重新冻结并按影响执行验收；若启动被blocked by policy拒绝，标BLOCKED并保留原文，不切换WMI或其他入口绕过。

## 获准后人工启动模板（本轮NOT_RUN）

以下命令尚未执行。需先取得8小时测试及7GiB隔离限额的明确批准，将批准记录填入参数；独立目录必须不存在。项目工作目录固定为D:\myAI\moyu-v1\moyu-agent。该旧长测入口已有真实时钟、存储和内存采样，但CPU/句柄及完整引用后验需同时安排独立监测和验收，不能仅凭入口result.json宣称完整8小时验收。

```powershell
$label='v133-8h-approved-unique'
$env:MOYU_ENV='SIMULATOR_TEST'
$env:MOYU_TEST_ROOT=Join-Path (Get-Location) "reports/$label"
$env:MOYU_DB=Join-Path $env:MOYU_TEST_ROOT 'agent.sqlite3'
if (Test-Path -LiteralPath $env:MOYU_TEST_ROOT) { throw '隔离目录已存在，拒绝覆盖' }
if ((Get-PSDrive D).Free -lt 22GB) { throw '可用容量不足22GiB' }
$policy=@{hard_limit_bytes=7516192768;soft_ratio=0.8;disk_reserve_bytes=67108864;delete_enabled=$false}
$policy | ConvertTo-Json | Set-Content -Encoding UTF8 "reports/$label.retention.json"
$approval=Read-Host '填写用户明确批准的时间、范围和原文记录（至少10字符）'
.\.venv\Scripts\python.exe scripts/nine_star_soak.py --seconds 28800 --label $label --allow-long-run --approval-note $approval
```

启动前复核冻结源码哈希、原模拟基底备份及历史清单。监测每分钟容量、CPU、内存、句柄、WAL、数据库响应、软告警和安全停止；结束后按最终1小时同等标准核对所有PNG/帧/决策引用、资源守恒、重复结算及历史哈希，并生成新报告。24小时应另获批准，按18GiB硬限额、55GiB最低空闲预算和86400秒执行，不复用8小时批准。
