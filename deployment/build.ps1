<#
.SYNOPSIS
    Builds a deployable Biometric Bridge package.

.DESCRIPTION
    Runs on a BUILD machine (needs Node 20+ and Python 3.12). The output is
    self-contained: the target machine needs neither Node nor Python.

    Steps: build the React portal into the backend's static directory, freeze the
    backend with PyInstaller, then stage WinSW and the operator scripts alongside it.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -SkipFrontend      # backend only, reusing the committed portal build
#>
[CmdletBinding()]
param(
    [switch]$SkipFrontend,
    [string]$OutputDir = "$PSScriptRoot\..\dist"
)

$ErrorActionPreference = 'Stop'
$root = Resolve-Path "$PSScriptRoot\.."
$venvPython = Join-Path $root '.venv\Scripts\python.exe'

Write-Host '=== Biometric Bridge build ===' -ForegroundColor Cyan

if (-not (Test-Path $venvPython)) {
    throw "Virtual environment not found at $venvPython. Run: python -m venv .venv; .\.venv\Scripts\pip install -r requirements-dev.txt"
}

# --- 1. Portal -------------------------------------------------------------
if (-not $SkipFrontend) {
    Write-Host '[1/4] Building the portal...' -ForegroundColor Yellow
    Push-Location (Join-Path $root 'frontend')
    try {
        # `npm ci` not `npm install`: a build must be reproducible from the lockfile.
        npm ci
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Portal build failed.' }
    } finally {
        Pop-Location
    }
} else {
    Write-Host '[1/4] Skipping portal build (using the committed one).' -ForegroundColor DarkGray
}

$indexPath = Join-Path $root 'backend\app\static\index.html'
if (-not (Test-Path $indexPath)) {
    throw 'No portal build found. Run without -SkipFrontend at least once.'
}

# --- 2. Tests --------------------------------------------------------------
Write-Host '[2/4] Running the test suite...' -ForegroundColor Yellow
Push-Location $root
try {
    & $venvPython -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed. Not packaging a broken build.' }
} finally {
    Pop-Location
}

# --- 3. Freeze -------------------------------------------------------------
Write-Host '[3/4] Freezing the backend with PyInstaller...' -ForegroundColor Yellow
Push-Location (Join-Path $root 'backend')
try {
    # PyInstaller logs progress to stderr; under Windows PowerShell 5.1 with 'Stop'
    # that is raised as an error, so judge success by the exit code instead.
    $ErrorActionPreference = 'Continue'
    & $venvPython -m PyInstaller --noconfirm --clean biometric_bridge.spec 2>&1 | ForEach-Object { "$_" }
    $pyInstallerExit = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($pyInstallerExit -ne 0) { throw 'PyInstaller failed.' }
} finally {
    Pop-Location
}

# --- 4. Stage --------------------------------------------------------------
Write-Host '[4/4] Staging the package...' -ForegroundColor Yellow
if (Test-Path $OutputDir) { Remove-Item $OutputDir -Recurse -Force }
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

Copy-Item (Join-Path $root 'backend\dist\BiometricBridge') (Join-Path $OutputDir 'BiometricBridge') -Recurse
Copy-Item (Join-Path $PSScriptRoot '*.ps1') $OutputDir
Copy-Item (Join-Path $PSScriptRoot 'winsw') (Join-Path $OutputDir 'winsw') -Recurse
Copy-Item (Join-Path $root 'docs') (Join-Path $OutputDir 'docs') -Recurse
Copy-Item (Join-Path $root 'README.md') $OutputDir

$winswExe = Join-Path $OutputDir 'winsw\WinSW.exe'
if (-not (Test-Path $winswExe)) {
    Write-Warning @'
WinSW.exe is not vendored in deployment\winsw\.
Download WinSW-x64.exe from https://github.com/winsw/winsw/releases,
rename it to WinSW.exe, and place it in deployment\winsw\ before shipping.
install.ps1 will refuse to run without it.
'@
}

$zip = Join-Path (Split-Path $OutputDir -Parent) 'BiometricBridge-package.zip'
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$OutputDir\*" -DestinationPath $zip

Write-Host ''
Write-Host "Package staged : $OutputDir" -ForegroundColor Green
Write-Host "Archive        : $zip" -ForegroundColor Green
Write-Host 'Copy the archive to the target machine and run install.ps1 as Administrator.'
