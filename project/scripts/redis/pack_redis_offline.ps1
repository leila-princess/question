# Pack Redis offline bundle (wheels + optional Windows redis-server). Run on online machine.
# Usage:
#   cd F:\antd-demo1
#   .\scripts\redis\pack_redis_offline.ps1
#   .\scripts\redis\pack_redis_offline.ps1 -DownloadServer

param(
    [string]$ProjectRoot = 'F:\antd-demo1',
    [string]$OutDir = '',
    [string]$CondaBase = '',
    [string]$MineruEnv = 'agent',
    [switch]$DownloadServer,
    [string]$RedisServerZipUrl = 'https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.zip'
)

$ErrorActionPreference = 'Stop'

if (-not $OutDir) {
    $OutDir = Join-Path $ProjectRoot 'offline_packages\redis'
}
if (-not $CondaBase) {
    $CondaBase = if ($env:CONDA_BASE) { $env:CONDA_BASE } else { 'C:\Users\hyx\anaconda3' }
}

$Python = Join-Path $CondaBase "envs\$MineruEnv\python.exe"
if (-not (Test-Path $Python)) {
    throw "Python not found: $Python (set -CondaBase / -MineruEnv)"
}

function Ensure-Dir($p) {
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Force -Path $p | Out-Null }
}

Write-Host "==> Redis offline bundle: $OutDir" -ForegroundColor Cyan
Ensure-Dir $OutDir
$wheelsDir = Join-Path $OutDir 'wheels'
Ensure-Dir $wheelsDir

$reqSrc = Join-Path $ProjectRoot 'backend\requirements-redis.txt'
$reqDst = Join-Path $OutDir 'requirements-redis.txt'
Copy-Item $reqSrc $reqDst -Force

Write-Host '==> pip download redis wheel ...'
& $Python -m pip download -r $reqDst -d $wheelsDir
if ($LASTEXITCODE -ne 0) { throw 'pip download failed' }

$filesToCopy = @(
    'redis.windows.conf',
    'install_redis_offline.ps1',
    'start_redis.ps1',
    'README.md'
)
foreach ($f in $filesToCopy) {
    $src = Join-Path $PSScriptRoot $f
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $OutDir $f) -Force
    }
}

if ($DownloadServer) {
    $zipPath = Join-Path $OutDir 'Redis-x64-5.0.14.1.zip'
    $serverDir = Join-Path $OutDir 'server'
    $minBytes = 10000000
    Write-Host "==> Download Redis for Windows: $RedisServerZipUrl" -ForegroundColor Cyan
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    curl.exe -L --retry 3 -o $zipPath $RedisServerZipUrl
    $zipSize = (Get-Item $zipPath).Length
    if ($zipSize -lt $minBytes) {
        throw "Redis zip incomplete ($zipSize bytes). Retry or copy Redis-x64-5.0.14.1.zip manually into $OutDir"
    }
    if (Test-Path $serverDir) { Remove-Item $serverDir -Recurse -Force }
    Expand-Archive -Path $zipPath -DestinationPath $serverDir -Force
    Write-Host "  extracted to $serverDir ($zipSize bytes)"
}

$wheelNames = (Get-ChildItem $wheelsDir -Filter *.whl | ForEach-Object { $_.Name } | Sort-Object) -join ', '
$manifest = @(
    'redis offline bundle',
    "created: $(Get-Date -Format o)",
    "python: $Python",
    "wheels: $wheelNames",
    "server_included: $DownloadServer"
) -join "`n"
Set-Content -Path (Join-Path $OutDir 'MANIFEST.txt') -Value $manifest -Encoding UTF8

Write-Host ''
Write-Host 'Done. Copy this folder to the offline machine:' -ForegroundColor Green
Write-Host "  $OutDir" -ForegroundColor Yellow
Write-Host ''
Write-Host 'Offline one-click install:' -ForegroundColor Green
Write-Host "  .\offline_packages\redis\install_redis_offline.ps1 -ProjectRoot $ProjectRoot -StartAfterInstall" -ForegroundColor Yellow
