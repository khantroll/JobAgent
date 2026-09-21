# Local dev — Simple UI
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

if (-not (Test-Path "venv\Scripts\uvicorn.exe")) {
    Write-Host "Creating venv..."
    python -m venv venv
    .\venv\Scripts\pip install -r requirements.txt
}

.\venv\Scripts\uvicorn.exe simple_ui.app:app --host 127.0.0.1 --port 8765 --reload
