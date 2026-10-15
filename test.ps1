param([switch]$SkipBrowser,[switch]$ReliabilityStress)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force reports | Out-Null
Push-Location frontend
try {
    node --test tests/console.test.mjs 2>&1 | Tee-Object -FilePath ../reports/console-unit.log
    $consoleUnitExit = $LASTEXITCODE
    npm run build 2>&1 | Tee-Object -FilePath ../reports/frontend-build.log
    $buildExit = $LASTEXITCODE
} finally { Pop-Location }
@{timestamp=(Get-Date).ToUniversalTime().ToString('o'); passed=($buildExit -eq 0); exitCode=$buildExit} | ConvertTo-Json | Set-Content -Encoding UTF8 reports/frontend-build-result.json
$testTemp = Join-Path $PSScriptRoot ('reports/pytest-' + [guid]::NewGuid().ToString())
& ./.venv/Scripts/python.exe -m pytest -q --basetemp=$testTemp --junitxml=reports/pytest.xml
$unitExit = $LASTEXITCODE
if (-not $SkipBrowser) {
    & ./.venv/Scripts/python.exe -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw '浏览器安装失败' }
    $previousDb = $env:MOYU_DB
    $env:MOYU_DB = Join-Path $PSScriptRoot ('reports/browser-' + [guid]::NewGuid().ToString() + '.sqlite3')
    $server = $null
    try {
        $server = Start-Process -FilePath (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') -ArgumentList '-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8001' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput 'reports/test-server.log' -RedirectStandardError 'reports/test-server-error.log'
        $healthy = $false
        for ($i=0; $i -lt 40; $i++) {
            if ($server.HasExited) { throw '测试服务启动失败，查看 reports/test-server-error.log' }
            try { $health = Invoke-RestMethod 'http://127.0.0.1:8001/api/health'; $healthy = $health.status -eq 'ok' } catch { }
            if ($healthy) { break }
            Start-Sleep -Milliseconds 250
        }
        if (-not $healthy) { throw '测试服务未就绪' }
        & ./.venv/Scripts/python.exe scripts/collect_official.py --db $env:MOYU_DB --output reports/test-official-snapshots --report reports/test-official-collection.json
        $collectionExit = $LASTEXITCODE
        & ./.venv/Scripts/python.exe scripts/console_browser_test.py --url http://127.0.0.1:8001
        $consoleBrowserExit = $LASTEXITCODE
        & ./.venv/Scripts/python.exe scripts/browser_test.py --url http://127.0.0.1:8001
        $browserExit = $LASTEXITCODE
        & ./.venv/Scripts/python.exe scripts/knowledge_browser_test.py --url http://127.0.0.1:8001
        $knowledgeExit = $LASTEXITCODE
        & ./.venv/Scripts/python.exe scripts/observation_browser_test.py --url http://127.0.0.1:8001
        $observationExit = $LASTEXITCODE
        & ./.venv/Scripts/python.exe scripts/research_browser_test.py --url http://127.0.0.1:8001
        $researchExit = $LASTEXITCODE
        if ($ReliabilityStress) {
            $taskStressDb=Join-Path $PSScriptRoot ('reports/stress-'+[guid]::NewGuid().ToString()+'.sqlite3')
            & ./.venv/Scripts/python.exe -c "import sqlite3,sys; src=sqlite3.connect(sys.argv[1]); dst=sqlite3.connect(sys.argv[2]); src.backup(dst)" $env:MOYU_DB $taskStressDb
            if ($LASTEXITCODE -ne 0) { throw '创建隔离压力测试副本失败' }
            $taskStressStarted=$false
            try {
                & ./.venv/Scripts/python.exe scripts/service_manager.py start --port 8030 --db $taskStressDb --watchdog --max-restarts 2
                if ($LASTEXITCODE -ne 0) { throw '压力测试服务启动失败，未接管已有端口' }
                $taskStressStarted=$true
                & ./.venv/Scripts/python.exe scripts/reliability_stress.py --url http://127.0.0.1:8030
                if ($LASTEXITCODE -ne 0) { throw '连续模拟压力测试失败' }
                & ./.venv/Scripts/python.exe scripts/reliability_browser_test.py --port 8030
                if ($LASTEXITCODE -ne 0) { throw '可靠性浏览器测试失败' }
            } finally {
                if ($taskStressStarted) { & ./.venv/Scripts/python.exe scripts/service_manager.py stop --port 8030 }
            }
        }
    } finally {
        if ($server) {
            # Windows venv启动器可能有独立Python子进程；仅释放本次启动的匹配服务。
            $taskChildren=Get-CimInstance Win32_Process -Filter "ParentProcessId=$($server.Id)"
            foreach ($taskChild in $taskChildren) {
                if ($taskChild.CommandLine -like '*moyu-agent*.venv*uvicorn backend.main:app*--port 8001*') { Stop-Process -Id $taskChild.ProcessId -ErrorAction SilentlyContinue }
            }
            if (-not $server.HasExited) { Stop-Process -Id $server.Id -ErrorAction SilentlyContinue }
        }
        $env:MOYU_DB = $previousDb
    }
} else { $browserExit = 0; $knowledgeExit = 0; $collectionExit = 0; $observationExit = 0; $researchExit = 0; $consoleBrowserExit = 0 }
& ./.venv/Scripts/python.exe scripts/report.py
if ($unitExit -ne 0 -or $browserExit -ne 0 -or $buildExit -ne 0 -or $knowledgeExit -ne 0 -or $collectionExit -ne 0 -or $observationExit -ne 0 -or $researchExit -ne 0 -or $consoleUnitExit -ne 0 -or $consoleBrowserExit -ne 0) { throw '存在未通过测试，详见 reports' }
