# Redis offline install: pip redis + Windows redis-server + .env snippet
# Usage on offline machine:
#   cd F:\antd-demo1
#   .\offline_packages\redis\install_redis_offline.ps1 -StartAfterInstall

param(
    [string]$ProjectRoot = 'F:\antd-demo1',
    [string]$BundleDir = '',
    [string]$CondaBase = '',
    [string]$MineruEnv = 'agent',
    [string]$RedisInstallDir = '',
    [switch]$SkipServer,
    [switch]$SkipPip,
    [switch]$StartAfterInstall
)

$ErrorActionPreference = 'Stop'

if (-not $BundleDir) {
    $BundleDir = Join-Path $ProjectRoot 'offline_packages\redis'
}
if (-not $RedisInstallDir) {
    $RedisInstallDir = Join-Path $ProjectRoot 'tools\redis'
}
if (-not $CondaBase) {
    $CondaBase = if ($env:CONDA_BASE) { $env:CONDA_BASE } else { 'C:\Users\hyx\anaconda3' }
}

$Python = Join-Path $CondaBase "envs\$MineruEnv\python.exe"

function Write-Step($msg) {
    Write-Host ''
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Ensure-Dir($p) {
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Force -Path $p | Out-Null }
}

function Merge-EnvSnippet($envPath, $snippetPath) {
    if (-not (Test-Path $snippetPath)) { return }
    $snippet = Get-Content $snippetPath -Raw -Encoding UTF8
    if (Test-Path $envPath) {
        $existing = Get-Content $envPath -Raw -Encoding UTF8
        if ($existing -match 'REDIS_HOST=') {
            Write-Host '  .env already has REDIS_*, skip merge' -ForegroundColor Yellow
            return
        }
        Add-Content -Path $envPath -Value "`n$snippet" -Encoding UTF8
    } else {
        Set-Content -Path $envPath -Value $snippet -Encoding UTF8
    }
    Write-Host "  merged Redis config into $envPath" -ForegroundColor Green
}

Write-Host '========================================' -ForegroundColor Green
Write-Host ' Redis offline install' -ForegroundColor Green
Write-Host " Bundle : $BundleDir" -ForegroundColor Green
Write-Host " Project: $ProjectRoot" -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Green

if (-not (Test-Path $BundleDir)) {
    throw "Bundle not found: $BundleDir`nRun pack_redis_offline.ps1 -DownloadServer on an online machine first."
}

if (-not $SkipPip) {
    Write-Step '1/3 Install Python package redis'
    if (-not (Test-Path $Python)) {
        throw "Python not found: $Python"
    }
    $wheels = Join-Path $BundleDir 'wheels'
    $req = Join-Path $BundleDir 'requirements-redis.txt'
    if (-not (Test-Path $wheels)) { throw "Missing wheels dir: $wheels" }
    & $Python -m pip install --no-index --find-links=$wheels -r $req
    & $Python -c "import redis; print('redis', redis.__version__, 'OK')"
} else {
    Write-Host '  skip pip (-SkipPip)'
}

if (-not $SkipServer) {
    Write-Step "2/3 Install redis-server to $RedisInstallDir"
    Ensure-Dir $RedisInstallDir
    $serverSrc = Join-Path $BundleDir 'server'
    if (Test-Path $serverSrc) {
        $inner = Get-ChildItem $serverSrc -Directory | Select-Object -First 1
        $copyFrom = if ($inner -and (Test-Path (Join-Path $inner.FullName 'redis-server.exe'))) {
            $inner.FullName
        } else {
            $serverSrc
        }
        Copy-Item (Join-Path $copyFrom 'redis-server.exe') $RedisInstallDir -Force
        if (Test-Path (Join-Path $copyFrom 'redis-cli.exe')) {
            Copy-Item (Join-Path $copyFrom 'redis-cli.exe') $RedisInstallDir -Force
        }
    } else {
        Write-Warning 'No server\ in bundle (pack with -DownloadServer)'
        Write-Warning 'Install Memurai / Redis for Windows manually, or re-pack on online machine'
    }
    $confSrc = Join-Path $BundleDir 'redis.windows.conf'
    if (Test-Path $confSrc) {
        Copy-Item $confSrc (Join-Path $RedisInstallDir 'redis.windows.conf') -Force
    }
    $startSrc = Join-Path $BundleDir 'start_redis.ps1'
    if (Test-Path $startSrc) {
        Copy-Item $startSrc (Join-Path $RedisInstallDir 'start_redis.ps1') -Force
    }
} else {
    Write-Host '  skip server (-SkipServer)'
}

Write-Step '3/3 Merge .env Redis settings'
$snippet = Join-Path $BundleDir 'env_redis_snippet.txt'
if (-not (Test-Path $snippet)) {
    @'
# --- Redis (ZNWD session memory, needs localhost:6379) ---
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=
REDIS_MEMORY_TTL=1800
REDIS_MEMORY_MAX_MESSAGES=10
REDIS_MEMORY_COMPRESS_AFTER=8
REDIS_MEMORY_KEEP_RECENT=4
'@ | Set-Content $snippet -Encoding UTF8
}
Merge-EnvSnippet (Join-Path $ProjectRoot '.env') $snippet
Merge-EnvSnippet (Join-Path $ProjectRoot 'backend\.env') $snippet

if ($StartAfterInstall) {
    Write-Step 'Start Redis and ping'
    $start = Join-Path $RedisInstallDir 'start_redis.ps1'
    if (Test-Path $start) {
        & $start -RedisDir $RedisInstallDir
    } else {
        & (Join-Path $ProjectRoot 'scripts\redis\start_redis.ps1') -RedisDir $RedisInstallDir
    }
    if (Test-Path $Python) {
        & $Python -c "import redis; r=redis.Redis(host='127.0.0.1',port=6379,db=0,decode_responses=True); print('PING', r.ping())"
    }
}

Write-Host ''
Write-Host '========================================' -ForegroundColor Green
Write-Host ' Done. Next steps:' -ForegroundColor Green
Write-Host '  1. Start Redis: .\tools\redis\start_redis.ps1' -ForegroundColor Yellow
Write-Host '  2. Restart backend; log should show: Redis connected' -ForegroundColor Yellow
Write-Host '  3. Test: python -c "import redis; print(redis.Redis().ping())"' -ForegroundColor Yellow
Write-Host '========================================' -ForegroundColor Green
