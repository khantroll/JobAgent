# Build the web UI for https://nedragaardkeep.quest/jobagent/
# Run on Windows (requires Node/npm), then upload web\dist to the server via SFTP.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Error "npm not found. Install Node.js from https://nodejs.org/ then retry."
}

$env:VITE_BASE_PATH = "/jobagent/"
Write-Host "Building with VITE_BASE_PATH=$($env:VITE_BASE_PATH)"

Push-Location web
if (Test-Path package-lock.json) { npm ci } else { npm install }
npm run build
Pop-Location

if (-not (Test-Path "web\dist\index.html")) {
    Write-Error "Build failed: web\dist\index.html missing"
}

Write-Host ""
Write-Host "OK. Upload this folder to the server via SFTP:"
Write-Host "  Local:  $(Resolve-Path web\dist)"
Write-Host "  Remote: /var/www/my_webapp__4/www/web/dist"
Write-Host "  (same level as orchestrator.py — like AgentTrader files in my_webapp__3/www)"
Write-Host ""
Write-Host "Then on the server (skip npm):"
Write-Host "  cd /var/www/my_webapp__4/www/job_agent"
Write-Host "  cd /var/www/my_webapp__4/www"
Write-Host "  sudo SKIP_WEB_BUILD=1 API_TOKEN='...' bash deploy/install-yunohost.sh"
