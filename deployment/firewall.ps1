<#
.SYNOPSIS
    Manages the inbound firewall rule for the portal.

.DESCRIPTION
    The rule is scoped to LocalSubnet, not Any. The portal runs over plain HTTP, so
    the session cookie and the sign-in password are readable by anyone who can see
    the traffic - it must not be reachable from beyond the local network.

    Outbound rules are not created: Windows allows outbound by default, and the
    bridge only needs HTTPS 443 to HRMS and TCP 4370 to the terminals.
#>
[CmdletBinding()]
param(
    [int]$Port = 8080,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$ruleName = 'Biometric Bridge Portal'

Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule

if ($Remove) {
    Write-Host 'Firewall rule removed.' -ForegroundColor Yellow
    return
}

New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $Port -RemoteAddress LocalSubnet -Profile Any | Out-Null

Write-Host "Inbound TCP $Port allowed from the local subnet only." -ForegroundColor Green
