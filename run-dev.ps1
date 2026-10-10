<#
.SYNOPSIS
    Runs Biometric Bridge locally for development.

.DESCRIPTION
    Starts the backend with a repo-local data directory (.dev\) instead of
    C:\ProgramData, so no Administrator rights are needed and a local run can never
    disturb an installed service's database.

    The portal is served from the committed build in backend\app\static, so this one
    command gives you the whole application at http://localhost:10255.

    Use -Reload while editing backend code. Use frontend\npm run dev in a second
    terminal if you are editing the portal — it proxies the API to this process.

.EXAMPLE
    .\run-dev.ps1
    .\run-dev.ps1 -Reload
    .\run-dev.ps1 -Port 9000 -LogLevel DEBUG
#>
[CmdletBinding()]
param(
    [int]$Port = 10255,
    [switch]$Reload,
    [ValidateSet('DEBUG', 'INFO', 'WARNING', 'ERROR')]
    [string]$LogLevel = 'INFO',
    [switch]$Fresh
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$dataRoot = Join-Path $root '.dev'

if (-not (Test-Path $python)) {
    Write-Host 'No virtual environment found. Creating one...' -ForegroundColor Yellow
    python -m venv (Join-Path $root '.venv')
    & $python -m pip install --upgrade pip
    & $python -m pip install -r (Join-Path $root 'requirements-dev.txt')
}

if ($Fresh -and (Test-Path $dataRoot)) {
    Write-Host 'Removing the local development database...' -ForegroundColor Yellow
    Remove-Item $dataRoot -Recurse -Force
}

$env:BIOMETRIC_BRIDGE_DATA_ROOT = $dataRoot
$env:BIOMETRIC_BRIDGE_APP_ENV = 'development'
$env:BIOMETRIC_BRIDGE_LOG_LEVEL = $LogLevel
$env:BIOMETRIC_BRIDGE_PORT = "$Port"

$portalBuilt = Test-Path (Join-Path $root 'backend\app\static\index.html')

Write-Host ''
Write-Host '  Biometric Bridge (development)' -ForegroundColor Cyan
Write-Host "  Portal    : http://localhost:$Port" -ForegroundColor Green
Write-Host "  API docs  : http://localhost:$Port/api/docs"
Write-Host "  Data      : $dataRoot"
if (-not $portalBuilt) {
    Write-Host '  Portal    : NOT BUILT - API only. Run: cd frontend; npm install; npm run build' -ForegroundColor Yellow
}
Write-Host '  First run creates the admin account in the browser - there are no defaults.'
Write-Host '  Ctrl+C to stop.'
Write-Host ''

$arguments = @('-m', 'uvicorn', 'app.main:app', '--app-dir', 'backend', '--host', '127.0.0.1', '--port', "$Port")
if ($Reload) { $arguments += @('--reload', '--reload-dir', 'backend/app') }

& $python @arguments
