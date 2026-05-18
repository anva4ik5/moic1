# Quick launcher for the agent.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path .venv-agent)) {
    python -m venv .venv-agent
    .\.venv-agent\Scripts\python.exe -m pip install --upgrade pip
    .\.venv-agent\Scripts\pip.exe install -r agent\requirements.txt
}
$server = if ($env:MONITOR_SERVER) { $env:MONITOR_SERVER } else { 'http://localhost:8765' }
.\.venv-agent\Scripts\python.exe -m agent.main --server $server
