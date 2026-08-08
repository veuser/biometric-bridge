<# Restarts the Biometric Bridge service. #>
[CmdletBinding()]
param([string]$InstallDir = "$env:ProgramFiles\BiometricBridge")
$ErrorActionPreference = 'Stop'
& "$InstallDir\winsw\WinSW.exe" restart "$InstallDir\winsw\BiometricBridge.xml"
Write-Host 'Restart requested. Run status.ps1 to confirm it is healthy.' -ForegroundColor Green
