<#
.SYNOPSIS
    Reports service state, portal health and recent errors.

.DESCRIPTION
    The first thing to run when someone says "attendance has stopped appearing".
    Checks the Windows service, the health endpoint, and the tail of the log.
#>
[CmdletBinding()]
param([int]$Port = 10255)

$ErrorActionPreference = 'Continue'
$DataRoot = "$env:ProgramData\BiometricBridge"

Write-Host '=== Biometric Bridge status ===' -ForegroundColor Cyan

$service = Get-Service -Name 'BiometricBridge' -ErrorAction SilentlyContinue
if ($service) {
    $colour = if ($service.Status -eq 'Running') { 'Green' } else { 'Red' }
    Write-Host ("Service    : {0} ({1} startup)" -f $service.Status, $service.StartType) -ForegroundColor $colour
} else {
    Write-Host 'Service    : NOT INSTALLED' -ForegroundColor Red
}

try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
    Write-Host ("Portal     : {0} on port {1}" -f $health.status, $Port) -ForegroundColor Green
} catch {
    Write-Host ("Portal     : not responding on port {0}" -f $Port) -ForegroundColor Red
}

$db = Join-Path $DataRoot 'data\biometric_bridge.db'
if (Test-Path $db) {
    $size = [math]::Round((Get-Item $db).Length / 1MB, 2)
    Write-Host ("Database   : {0} MB" -f $size)
} else {
    Write-Host 'Database   : not created yet' -ForegroundColor Yellow
}

$log = Join-Path $DataRoot 'logs\biometric_bridge.log'
if (Test-Path $log) {
    Write-Host ''
    Write-Host 'Recent errors and warnings:' -ForegroundColor Cyan
    $recent = Get-Content $log -Tail 400 |
        Where-Object { $_ -match '"level": "(ERROR|WARNING|CRITICAL)"' } |
        Select-Object -Last 10
    if ($recent) { $recent | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkYellow } }
    else { Write-Host '  None in the recent log.' -ForegroundColor Green }
} else {
    Write-Host 'Log        : not created yet' -ForegroundColor Yellow
}

Write-Host ''
Write-Host "Portal : http://${env:COMPUTERNAME}:$Port"
Write-Host "Logs   : $log"
