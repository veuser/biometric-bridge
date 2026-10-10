<#
.SYNOPSIS
    Installs Biometric Bridge as a Windows service.

.DESCRIPTION
    Run as Administrator from an extracted package directory.

    Creates the program and data directories, registers the service to start on
    boot, opens the portal port to the local subnet only, starts the service and
    waits for /health to answer.

    Re-running upgrades in place: the service is stopped, the program files are
    replaced, and the service restarted. ProgramData is never touched, so
    configuration and synchronization state survive an upgrade.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -Port 9090 -SkipFirewall
    .\install.ps1 -Port 8080          # revert to the previous default
#>
[CmdletBinding()]
param(
    [int]$Port = 10255,
    [switch]$SkipFirewall,
    [string]$InstallDir = "$env:ProgramFiles\BiometricBridge"
)

$ErrorActionPreference = 'Stop'
$ServiceId = 'BiometricBridge'
$DataRoot = "$env:ProgramData\BiometricBridge"
$source = $PSScriptRoot

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this script from an elevated PowerShell session (Run as Administrator).'
    }
}

Assert-Administrator
Write-Host '=== Installing Biometric Bridge ===' -ForegroundColor Cyan

$winswSource = Join-Path $source 'winsw\WinSW.exe'
if (-not (Test-Path $winswSource)) {
    throw @'
winsw\WinSW.exe is missing from this package.
Download WinSW-x64.exe from https://github.com/winsw/winsw/releases, rename it to
WinSW.exe, and place it in the winsw folder next to this script.
'@
}

# --- 1. Directories --------------------------------------------------------
Write-Host '[1/7] Creating directories...' -ForegroundColor Yellow
foreach ($dir in @($DataRoot, "$DataRoot\data", "$DataRoot\logs", "$DataRoot\config", "$DataRoot\backups")) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
}

# The database holds the HRMS API key. With encryption at rest currently disabled,
# these ACLs are the control on it - so inheritance is removed rather than merely
# added to, otherwise "Users" keeps whatever ProgramData grants by default.
Write-Host '[2/7] Restricting permissions on the data directory...' -ForegroundColor Yellow
$acl = Get-Acl $DataRoot
$acl.SetAccessRuleProtection($true, $false)
foreach ($account in @('SYSTEM', 'Administrators')) {
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $account, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -Path $DataRoot -AclObject $acl

# --- 2. Stop an existing service ------------------------------------------
$existing = Get-Service -Name $ServiceId -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host '[3/7] Stopping the existing service for upgrade...' -ForegroundColor Yellow
    & "$InstallDir\winsw\WinSW.exe" stop "$InstallDir\winsw\BiometricBridge.xml" 2>$null
    Start-Sleep -Seconds 3
} else {
    Write-Host '[3/7] No existing service found; this is a fresh install.' -ForegroundColor DarkGray
}

# --- 3. Program files ------------------------------------------------------
Write-Host '[4/7] Copying program files...' -ForegroundColor Yellow
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
Copy-Item (Join-Path $source 'BiometricBridge') $InstallDir -Recurse -Force
Copy-Item (Join-Path $source 'winsw') $InstallDir -Recurse -Force
if (Test-Path (Join-Path $source 'docs')) {
    Copy-Item (Join-Path $source 'docs') $InstallDir -Recurse -Force
}

$serviceXml = Join-Path $InstallDir 'winsw\BiometricBridge.xml'
if ($Port -ne 10255) {
    (Get-Content $serviceXml) -replace 'name="BIOMETRIC_BRIDGE_PORT" value="10255"',
        "name=`"BIOMETRIC_BRIDGE_PORT`" value=`"$Port`"" | Set-Content $serviceXml -Encoding utf8
}

# --- 4. Register the service ----------------------------------------------
Write-Host '[5/7] Registering the Windows service...' -ForegroundColor Yellow
if (-not $existing) {
    & "$InstallDir\winsw\WinSW.exe" install $serviceXml
    if ($LASTEXITCODE -ne 0) { throw 'Service registration failed.' }
}

# --- 5. Firewall -----------------------------------------------------------
if (-not $SkipFirewall) {
    Write-Host '[6/7] Adding a firewall rule for the local subnet...' -ForegroundColor Yellow
    $ruleName = 'Biometric Bridge Portal'
    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    # RemoteAddress LocalSubnet, not Any: the portal runs over plain HTTP, so it
    # must not be reachable from beyond the local network.
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $Port -RemoteAddress LocalSubnet -Profile Any | Out-Null
} else {
    Write-Host '[6/7] Skipping the firewall rule as requested.' -ForegroundColor DarkGray
}

# --- 6. Start and verify ---------------------------------------------------
Write-Host '[7/7] Starting the service...' -ForegroundColor Yellow
& "$InstallDir\winsw\WinSW.exe" start $serviceXml

$healthy = $false
foreach ($attempt in 1..30) {
    Start-Sleep -Seconds 2
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        if ($response.status -eq 'healthy') { $healthy = $true; break }
    } catch {
        # Still starting: migrations run on first boot.
    }
}

Write-Host ''
if (-not $healthy) {
    Write-Warning "The service did not report healthy within 60 seconds."
    Write-Warning "Check $DataRoot\logs\biometric_bridge.log and the WinSW log beside it."
    exit 1
}

$hostname = $env:COMPUTERNAME
Write-Host '=== Installation complete ===' -ForegroundColor Green
Write-Host ''
Write-Host "  Portal      : http://${hostname}:$Port    (or http://localhost:$Port)"
Write-Host "  Data        : $DataRoot"
Write-Host "  Logs        : $DataRoot\logs\biometric_bridge.log"
Write-Host "  Service     : $ServiceId (starts automatically on boot)"
Write-Host ''
Write-Host 'Next steps:' -ForegroundColor Cyan
Write-Host '  1. Open the portal and create the administrator account.'
Write-Host '     No default credentials exist - the first visit sets one up.'
Write-Host '  2. Add your biometric devices and use Test Connection on each.'
Write-Host '  3. Enter the HRMS URL, API key and endpoint profile (docs\HRMS-PROFILE.md).'
Write-Host '  4. Review the first-sync lookback in Settings, then enable automatic sync.'
