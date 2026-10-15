param([int]$Port=8000)
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
& ./.venv/Scripts/python.exe scripts/service_manager.py stop --port $Port
if ($LASTEXITCODE -ne 0) { throw '停止失败或身份未确认；查看 status.ps1 和管理日志。主动停止标志不会自动清除。' }
