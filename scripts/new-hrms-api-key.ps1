<#
.SYNOPSIS
    Generates an integration API key for the Biometric Bridge and prints the SQL to
    register it in HRMS.

.DESCRIPTION
    HRMS stores only the SHA-256 of the key, so the secret exists exactly once: in the
    output of this script. It cannot be recovered afterwards — if it is lost, issue a
    new key and revoke the old one.

    This script does not touch a database. It prints the INSERT for a DBA to run,
    because registering a credential against a tenant is a deliberate act and should
    leave a trace in whatever change process the customer uses.

    An admin UI for issuing and revoking these is not built yet; this is the interim.

.PARAMETER Name
    Operator-facing label, e.g. "Hyderabad Biometric Bridge".

.PARAMETER DataKey
    The OWNING tenant's DataKey. Every punch posted with this key is written into that
    tenant. Get it from AuthP: the tenant's GetTenantDataKey() value, typically
    something like "1." or "1.4.".

    Getting this wrong writes one customer's attendance into another's records, so it
    is a required parameter with no default.

.PARAMETER ExpiresInDays
    Optional lifetime. Omit for a key that does not expire.

.EXAMPLE
    .\new-hrms-api-key.ps1 -Name "Hyderabad Biometric Bridge" -DataKey "1."

.EXAMPLE
    .\new-hrms-api-key.ps1 -Name "Bangalore Bridge" -DataKey "1.7." -ExpiresInDays 365
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$DataKey,
    [int]$ExpiresInDays = 0
)

$ErrorActionPreference = 'Stop'

# 256 bits from a CSPRNG. Base64url so it survives headers, JSON and copy/paste
# without escaping, and carries no characters an operator will mistype.
#
# RNGCryptoServiceProvider rather than RandomNumberGenerator.Fill: this has to run
# under Windows PowerShell 5.1 on a customer's server, which is .NET Framework, where
# Fill does not exist. Get-Random is not an option — it is not cryptographic.
$bytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RNGCryptoServiceProvider]::new()
try {
    $rng.GetBytes($bytes)
}
finally {
    $rng.Dispose()
}
$secret = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')

$sha = [System.Security.Cryptography.SHA256]::Create()
try {
    $hashBytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($secret))
}
finally {
    $sha.Dispose()
}
$hash = -join ($hashBytes | ForEach-Object { $_.ToString('x2') })

$prefix = $secret.Substring(0, 12)

if ($ExpiresInDays -gt 0) {
    $expiresAt = "'" + (Get-Date).ToUniversalTime().AddDays($ExpiresInDays).ToString('yyyy-MM-ddTHH:mm:ssZ') + "'"
}
else {
    $expiresAt = 'NULL'
}

# Escape single quotes so a label containing an apostrophe cannot break the statement.
$safeName = $Name.Replace("'", "''")
$safeDataKey = $DataKey.Replace("'", "''")

Write-Host ''
# ASCII only in console output: the Windows console codepage mangles em-dashes.
Write-Host '=== SECRET - shown once, never recoverable ===' -ForegroundColor Yellow
Write-Host ''
Write-Host "  $secret" -ForegroundColor Green
Write-Host ''
Write-Host 'Paste it into the bridge portal: HRMS Configuration -> API Key.'
Write-Host 'It is encrypted at rest there (Windows DPAPI) and never displayed again.'
Write-Host ''
Write-Host '=== Run this against the TENANT database ===' -ForegroundColor Cyan
Write-Host ''

@"
INSERT INTO "BaseSch"."IntegrationApiKeys"
    ("Name", "KeyPrefix", "KeyHash", "Scope", "DataKey", "IsActive", "ExpiresAt", "CreatedDate")
VALUES
    ('$safeName', '$prefix', '$hash', 'attendance.write', '$safeDataKey', true, $expiresAt, now() at time zone 'utc');
"@

Write-Host ''
Write-Host 'To revoke later (keeps the audit trail, unlike a DELETE):' -ForegroundColor Cyan
Write-Host ''
@"
UPDATE "BaseSch"."IntegrationApiKeys"
   SET "IsActive" = false, "RevokedAt" = now() at time zone 'utc'
 WHERE "KeyPrefix" = '$prefix';
"@
Write-Host ''
