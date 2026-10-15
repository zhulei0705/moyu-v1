param([int]$Port=8000)
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
& ./.venv/Scripts/python.exe scripts/service_manager.py status --port $Port
if ($LASTEXITCODE -ne 0) { throw '状态查询失败' }
