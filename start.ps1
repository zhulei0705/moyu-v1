param([switch]$NoBrowser,[switch]$SkipSetup,[switch]$Watchdog,[switch]$IndependentHost,[int]$Port=8000,[int]$MaxRestarts=3,[double]$BaseDelay=2,[int]$StartupTimeout=30,[string]$Database='')
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { throw "端口 $Port 已占用，请先运行 status.ps1；不会覆盖未知服务" }
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败' }
}
if (-not $SkipSetup) {
    & ./.venv/Scripts/python.exe -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Python 依赖安装失败' }
    Push-Location frontend
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw 'npm 依赖安装失败' }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
    } finally { Pop-Location }
}
$taskArgs=@('scripts/service_manager.py','start','--port',"$Port",'--max-restarts',"$MaxRestarts",'--base-delay',"$BaseDelay",'--startup-timeout',"$StartupTimeout")
if ($Watchdog) { $taskArgs+='--watchdog' }
if ($IndependentHost) { $taskArgs+='--independent-host' }
if ($Database) { $taskArgs+=@('--db',$Database) }
& ./.venv/Scripts/python.exe @taskArgs
if ($LASTEXITCODE -ne 0) { throw '服务启动失败，查看 .runtime/service-端口 的日志' }
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
