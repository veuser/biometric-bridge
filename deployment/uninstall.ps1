<#
.SYNOPSIS
    Removes the Biometric Bridge service.

.DESCRIPTION
    Stops and unregisters the service, removes the firewall rule and deletes the
    program files.

    ProgramData is LEFT INTACT by default. It holds the device configuration, the
    HRMS settings and - most importantly - the per-device synchronization
    watermarks. Deleting those means the next install re-reads from its initial
    lookback window and may re-push attendance your HRMS already has.

    Pass -RemoveData to delete it as well. You will be asked to confirm.
#>
[CmdletBinding()]
param(
    [switch]$RemoveData,
    [string]$InstallDir = "$env:ProgramFiles\BiometricBridge"
)

$ErrorActionPreference = 'Stop'
$ServiceId = 'BiometricBridge'
$DataRoot = "$env:ProgramData\BiometricBridge"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated PowerShell session (Run as Administrator).'
}

Write-Host '=== Uninstalling Biometric Bridge ===' -ForegroundColor Cyan

$serviceXml = Join-Path $InstallDir 'winsw\BiometricBridge.xml'
if (Test-Path $serviceXml) {
    Write-Host 'Stopping and unregistering the service...' -ForegroundColor Yellow
    & "$InstallDir\winsw\WinSW.exe" stop $serviceXml 2>$null
    Start-Sleep -Seconds 3
    & "$InstallDir\winsw\WinSW.exe" uninstall $serviceXml 2>$null
} elseif (Get-Service -Name $ServiceId -ErrorAction SilentlyContinue) {
    Write-Warning "The service exists but $serviceXml is missing; remove it manually with sc.exe delete $ServiceId"
}

Write-Host 'Removing the firewall rule...' -ForegroundColor Yellow
Get-NetFirewallRule -DisplayName 'Biometric Bridge Portal' -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule

if (Test-Path $InstallDir) {
    Write-Host 'Removing program files...' -ForegroundColor Yellow
    Remove-Item $InstallDir -Recurse -Force
}

if ($RemoveData) {
    Write-Host ''
    Write-Warning "This will delete $DataRoot, including every device's synchronization"
    Write-Warning 'watermark. A future install would restart from its initial lookback'
    Write-Warning 'window and may re-send attendance your HRMS already has.'
    $answer = Read-Host 'Type DELETE to confirm'
    if ($answer -ceq 'DELETE') {
        Remove-Item $DataRoot -Recurse -Force
        Write-Host 'Application data removed.' -ForegroundColor Yellow
    } else {
        Write-Host 'Application data kept.' -ForegroundColor Green
    }
} else {
    Write-Host ''
    Write-Host "Application data kept at $DataRoot" -ForegroundColor Green
    Write-Host 'Pass -RemoveData to delete configuration and sync state as well.'
}

Write-Host ''
Write-Host '=== Uninstall complete ===' -ForegroundColor Green
