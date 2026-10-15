param([int]$Port=8000,[switch]$Watchdog,[switch]$IndependentHost,[int]$MaxRestarts=3,[double]$BaseDelay=2,[int]$StartupTimeout=30,[string]$Database='')
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
$taskArgs=@('scripts/service_manager.py','restart','--port',"$Port",'--max-restarts',"$MaxRestarts",'--base-delay',"$BaseDelay",'--startup-timeout',"$StartupTimeout")
if ($Watchdog) { $taskArgs+='--watchdog' }
if ($IndependentHost) { $taskArgs+='--independent-host' }
if ($Database) { $taskArgs+=@('--db',$Database) }
& ./.venv/Scripts/python.exe @taskArgs
if ($LASTEXITCODE -ne 0) { throw '重启失败；不会终止身份不明的占用进程' }
