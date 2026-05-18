# Quick launcher for the server.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) {
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\pip.exe install -r server\requirements.txt
}
.\.venv\Scripts\python.exe -m uvicorn server.main:app --host 0.0.0.0 --port 8765
