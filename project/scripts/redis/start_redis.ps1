# Start local Redis (portable). Usage: .\scripts\redis\start_redis.ps1

param(
    [string]$RedisDir = ''
)

$ErrorActionPreference = 'Stop'

if (-not $RedisDir) {
    $repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $candidates = @(
        (Join-Path $repoRoot 'tools\redis'),
        $PSScriptRoot
    )
    foreach ($c in $candidates) {
        $exe = Join-Path $c 'redis-server.exe'
        if (Test-Path $exe) { $RedisDir = $c; break }
    }
}

if (-not $RedisDir) {
    throw 'redis-server.exe not found. Run install_redis_offline.ps1 first.'
}

$server = Join-Path $RedisDir 'redis-server.exe'
$conf = Join-Path $RedisDir 'redis.windows.conf'
if (-not (Test-Path $server)) { throw "Missing $server" }
if (-not (Test-Path $conf)) { throw "Missing $conf" }

try {
    $tcp = Test-NetConnection -ComputerName 127.0.0.1 -Port 6379 -WarningAction SilentlyContinue
    if ($tcp.TcpTestSucceeded) {
        Write-Host 'Redis already listening on 127.0.0.1:6379, skip start.' -ForegroundColor Yellow
        exit 0
    }
} catch {
    # ignore if Test-NetConnection unavailable
}

Write-Host "Starting Redis: $server" -ForegroundColor Cyan
Write-Host "Config: $conf" -ForegroundColor Cyan
Start-Process -FilePath $server -ArgumentList "`"$conf`"" -WorkingDirectory $RedisDir
Start-Sleep -Seconds 2
Write-Host 'Redis started on 127.0.0.1:6379. Close its window to stop.' -ForegroundColor Green
