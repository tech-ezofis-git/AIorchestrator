# Wipe EZOFIS AP tickets on live Postgres (postgrev6southinddb).
# Run in PowerShell from anywhere:
#   D:\ezofis\v6\orchestrator\scripts\run_wipe_ezofis_tickets.ps1
#
# Or with password already set:
#   $env:PGPASSWORD = '...'
#   .\scripts\run_wipe_ezofis_tickets.ps1
#
# Skip the WIPE confirmation prompt:
#   .\scripts\run_wipe_ezofis_tickets.ps1 -Force

param(
    [switch]$Force,
    [switch]$PreviewOnly
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# Live flexible-server defaults (override via env if needed).
if (-not $env:PGHOST) { $env:PGHOST = "postgrev6southinddb.postgres.database.azure.com" }
if (-not $env:PGUSER) { $env:PGUSER = "postgrev6southindadmin" }
if (-not $env:PGPORT) { $env:PGPORT = "5432" }

if (-not $env:PGPASSWORD) {
    $secure = Read-Host "Postgres password for $($env:PGUSER)@$($env:PGHOST)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) | Out-Null
    }
}

$pyArgs = @(
    "scripts\run_wipe_ezofis_tickets.py",
    "--host", $env:PGHOST,
    "--user", $env:PGUSER,
    "--password", $env:PGPASSWORD
)

Write-Host "Host=$($env:PGHOST)  User=$($env:PGUSER)" -ForegroundColor DarkGray
Write-Host "1) Preview tables..." -ForegroundColor Cyan
py -3 @pyArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($PreviewOnly) {
    Write-Host "Preview only (-PreviewOnly). No truncate." -ForegroundColor Green
    exit 0
}

if (-not $Force) {
    $confirm = Read-Host "Type WIPE to truncate all ticket tables"
    if ($confirm -ne "WIPE") {
        Write-Host "Cancelled."
        exit 0
    }
}

Write-Host "2) Execute wipe..." -ForegroundColor Yellow
py -3 @pyArgs --execute
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host @"

Done. Postgres tickets wiped.

3) If Global Search still shows old results, flush Redis on the agents host:
   docker compose exec redis redis-cli FLUSHDB

"@ -ForegroundColor Green
