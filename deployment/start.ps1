<# Starts the Biometric Bridge service. #>
[CmdletBinding()]
param([string]$InstallDir = "$env:ProgramFiles\BiometricBridge")
$ErrorActionPreference = 'Stop'
& "$InstallDir\winsw\WinSW.exe" start "$InstallDir\winsw\BiometricBridge.xml"
Write-Host 'Start requested. Run status.ps1 to confirm it is healthy.' -ForegroundColor Green
