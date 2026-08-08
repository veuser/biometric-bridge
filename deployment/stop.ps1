<#
.SYNOPSIS
    Stops the Biometric Bridge service.

.DESCRIPTION
    A sync in flight is allowed to finish; a full device buffer read takes several
    seconds and killing it mid-write is worse than waiting. Any lock left behind by
    a hard kill is cleared automatically on the next start.
#>
[CmdletBinding()]
param([string]$InstallDir = "$env:ProgramFiles\BiometricBridge")
$ErrorActionPreference = 'Stop'
& "$InstallDir\winsw\WinSW.exe" stop "$InstallDir\winsw\BiometricBridge.xml"
Write-Host 'Service stopped.' -ForegroundColor Yellow
