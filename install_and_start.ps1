# Win10 offline one-click install and start
# Usage (on target machine, no network):
#   D:\SYFAXTYX\install_and_start.ps1 -InstallRoot D:\SYFAXTYX
#   D:\SYFAXTYX\install_and_start.ps1 -InstallRoot D:\SYFAXTYX -SkipInstall -StartOnly

param(
    [string]$InstallRoot = "",
    [string]$ProjectRoot = "",
    [string]$CondaBase = "",
    [string]$MineruEnv = "agent",
    [switch]$SkipInstall,
    [switch]$StartOnly,
    [switch]$SkipRestore,
    [switch]$SkipMiniconda,
    [switch]$SkipStart
)

$ErrorActionPreference = "Stop"
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$commonPath = Join-Path $scriptDir "scripts\deploy\_deploy_common.ps1"
if (-not (Test-Path $commonPath)) {
    $commonPath = Join-Path $scriptDir "_deploy_common.ps1"
}
. $commonPath

if (-not $InstallRoot) {
    $InstallRoot = (Resolve-Path $scriptDir).Path
}
if (-not $ProjectRoot) {
    $ProjectRoot = Join-Path $InstallRoot "project"
}

$configPath = Join-Path $InstallRoot "config\install_config.json"
$envTemplate = Join-Path $InstallRoot "config\.env.template"
$deployScripts = Join-Path $InstallRoot "scripts\deploy"

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host " Win10 Offline Install & Start" -ForegroundColor Cyan
Write-Host " InstallRoot: $InstallRoot" -ForegroundColor Cyan
Write-Host " ProjectRoot: $ProjectRoot" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan

$freeGb = Get-FreeSpaceGB $InstallRoot
Write-Host "Free disk: ${freeGb} GB"
if ($freeGb -lt 30 -and -not $SkipInstall) {
    Write-Warning "Recommend >= 60 GB free for full install"
}

if (-not $SkipInstall -and -not $StartOnly) {

    # --- Load or create config ---
    if (-not (Test-Path $configPath)) {
        throw "Missing $configPath — re-copy bundle or run pack_win10_full.ps1"
    }
    $cfg = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $cfg.ProjectRoot = $ProjectRoot
    $cfg.InstallRoot = $InstallRoot
    if ($CondaBase) { $cfg.CondaBase = $CondaBase }
    if (-not $cfg.CondaBase) { $cfg.CondaBase = Join-Path $InstallRoot "runtime\miniconda3" }

    # --- 1. Miniconda ---
    if (-not $SkipMiniconda) {
        Write-DeployStep "1/8 Install Miniconda"
        $condaExe = Join-Path $InstallRoot "env\Miniconda3-latest-Windows-x86_64.exe"
        if (-not (Test-Path $condaExe)) {
            $condaExe = Get-ChildItem (Join-Path $InstallRoot "env") -Filter "Miniconda*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
        }
        $condaTarget = $cfg.CondaBase
        if (-not (Test-Path (Join-Path $condaTarget "Scripts\conda.exe"))) {
            if (-not $condaExe) { throw "Miniconda installer not found in env/" }
            Ensure-Dir $condaTarget
            Write-Host "  silent install -> $condaTarget"
            $installArgs = @("/InstallationType=JustMe", "/RegisterPython=0", "/S", "/D=$condaTarget")
            Start-Process -FilePath $condaExe -ArgumentList $installArgs -Wait -NoNewWindow
        } else {
            Write-Host "  Miniconda already installed: $condaTarget"
        }
        $condaExePath = Join-Path $condaTarget "Scripts\conda.exe"
        $envPath = Join-Path $condaTarget "envs\$MineruEnv"
        if (-not (Test-Path (Join-Path $envPath "python.exe"))) {
            Write-Host "  create env $MineruEnv (python 3.10) ..."
            & $condaExePath create -y -n $MineruEnv python=3.10
        }
    } else {
        if (-not $CondaBase) { $CondaBase = $cfg.CondaBase }
    }

    $CondaBase = if ($CondaBase) { $CondaBase } else { $cfg.CondaBase }
    $python = Join-Path $CondaBase "envs\$MineruEnv\python.exe"

    # --- 2. pip offline ---
    Write-DeployStep "2/8 pip install (offline)"
    $wheels = Join-Path $InstallRoot "env\pip_wheels"
    $req = Join-Path $InstallRoot "env\requirements_offline.txt"
    if ((Test-Path $python) -and (Test-Path $wheels) -and (Test-Path $req)) {
        & $python -m pip install --no-index --find-links=$wheels -r $req
    } else {
        Write-Warning "  skip pip — python/wheels/req missing"
    }

    # --- 3. project code ---
    Write-DeployStep "3/8 Deploy project code"
    $projSrc = Join-Path $InstallRoot "project"
    if (-not (Test-Path $projSrc)) { throw "Missing project/ in bundle" }
    Ensure-Dir $ProjectRoot
    Robo-Mirror $projSrc $ProjectRoot -Quiet

    # --- 4. frontend deps ---
    Write-DeployStep "4/8 Deploy frontend (node_modules + .next)"
    $fdSrc = Join-Path $InstallRoot "fronted_deps"
    if (Test-Path $fdSrc) {
        Robo-Mirror (Join-Path $fdSrc "node_modules") (Join-Path $ProjectRoot "fronted\node_modules") -Quiet
        foreach ($f in @("package.json", "package-lock.json")) {
            $fs = Join-Path $fdSrc $f
            if (Test-Path $fs) { Copy-Item $fs (Join-Path $ProjectRoot "fronted\$f") -Force }
        }
    }
    $nextSrc = Join-Path $InstallRoot "fronted_deps\.next"
    if (-not (Test-Path $nextSrc)) { $nextSrc = Join-Path $InstallRoot "fronted_deps\next_build\.next" }
    if (Test-Path $nextSrc) {
        Robo-Mirror $nextSrc (Join-Path $ProjectRoot "fronted\.next") -Quiet
    } else {
        Write-Warning "  .next build not in bundle — run npm run build on dev machine and repack"
    }
    $pubSrc = Join-Path $InstallRoot "fronted_deps\public"
    if (Test-Path $pubSrc) {
        Robo-Mirror $pubSrc (Join-Path $ProjectRoot "fronted\public") -Quiet
    }

    # --- 5. models ---
    Write-DeployStep "5/8 Deploy models"
    Robo-Mirror (Join-Path $InstallRoot "models") (Join-Path $ProjectRoot "models") -Quiet

    # --- 6. runtime (neo4j/minio/redis/mysql) ---
    Write-DeployStep "6/8 Deploy runtime services"
    $rtNeo4j = Join-Path $InstallRoot "runtime\neo4j"
    $rtMinio = Join-Path $InstallRoot "runtime\minio"
    $rtRedis = Join-Path $InstallRoot "runtime\redis"
    $rtMysql = Join-Path $InstallRoot "runtime\mysql"

    if (Test-Path $rtNeo4j) {
        $neo4jDest = if ($cfg.Neo4jHome) { $cfg.Neo4jHome } else { Join-Path $InstallRoot "runtime\neo4j" }
        if ($neo4jDest -ne $rtNeo4j) { Robo-Mirror $rtNeo4j $neo4jDest -Quiet }
        $cfg.Neo4jHome = $neo4jDest
    }
    if (Test-Path $rtMinio) {
        $cfg.MinioExe = Join-Path $rtMinio "minio.exe"
        $cfg.MinioDataDir = Join-Path $rtMinio "data"
        Ensure-Dir $cfg.MinioDataDir
    }
    if (Test-Path $rtRedis) {
        $redisInstall = Join-Path $ProjectRoot "scripts\redis\install_redis_offline.ps1"
        if (-not (Test-Path $redisInstall)) {
            Ensure-Dir (Join-Path $ProjectRoot "scripts\redis")
            Copy-Item (Join-Path $rtRedis "install_redis_offline.ps1") (Join-Path $ProjectRoot "scripts\redis\") -Force -ErrorAction SilentlyContinue
            Copy-Item (Join-Path $rtRedis "start_redis.ps1") (Join-Path $ProjectRoot "scripts\redis\") -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path (Join-Path $rtRedis "install_redis_offline.ps1")) {
            & (Join-Path $rtRedis "install_redis_offline.ps1") -ProjectRoot $ProjectRoot -BundleDir $rtRedis -CondaBase $CondaBase -SkipPip
        }
    }
    if (Test-Path $rtMysql) {
        $cfg.MySqlBin = Join-Path $rtMysql "bin"
        $cfg.MySqlDataDir = Join-Path $rtMysql "data"
    }

    # --- 7. restore databases ---
    if (-not $SkipRestore) {
        Write-DeployStep "7/8 Restore databases"
        $restoreScript = Join-Path $deployScripts "restore_databases.ps1"
        if (Test-Path $restoreScript) {
            & $restoreScript -DataDir (Join-Path $InstallRoot "data") -ProjectRoot $ProjectRoot -InstallRoot $InstallRoot `
                -Neo4jHome $cfg.Neo4jHome -MySqlBin $cfg.MySqlBin -MinioDataDir $cfg.MinioDataDir
        }
    }

    # --- 8. config files ---
    Write-DeployStep "8/8 Generate .env, mineru.json, start-all.ps1"
    $vars = @{
        InstallRoot = $InstallRoot.Replace('\', '/')
        ProjectRoot = $ProjectRoot.Replace('\', '/')
        CondaBase = $CondaBase.Replace('\', '/')
    }
    if (Test-Path $envTemplate) {
        $envText = Get-Content $envTemplate -Raw -Encoding UTF8
        $envText = Expand-Template $envText $vars
        Write-Utf8NoBom (Join-Path $ProjectRoot ".env") $envText
    }

    $mineruTemplate = Join-Path $InstallRoot "config\mineru.json"
    if (Test-Path $mineruTemplate) {
        $mj = Get-Content $mineruTemplate -Raw -Encoding UTF8
        $mj = $mj.Replace("F:/antd-demo1", $ProjectRoot.Replace('\', '/'))
        $mj = $mj.Replace("F:\antd-demo1", $ProjectRoot.Replace('\', '/'))
        Write-Utf8NoBom (Join-Path $ProjectRoot "mineru.json") $mj
    }

    $cfg | ConvertTo-Json -Depth 5 | Out-File $configPath -Encoding utf8
    & (Join-Path $deployScripts "generate_start_all.ps1") -ProjectRoot $ProjectRoot -ConfigPath $configPath

    # copy deploy scripts into project for healthcheck
    if (Test-Path $deployScripts) {
        $projDeploy = Join-Path $ProjectRoot "scripts\deploy"
        Ensure-Dir $projDeploy
        Copy-Item (Join-Path $deployScripts "*") $projDeploy -Recurse -Force
    }

    Write-Host ""
    Write-Host "Install complete." -ForegroundColor Green
}

if (-not $SkipStart) {
    Write-DeployStep "Starting all services"
    $startAll = Join-Path $ProjectRoot "start-all.ps1"
    if (-not (Test-Path $startAll)) { throw "start-all.ps1 not found — run install first" }
    & $startAll
    Start-Sleep -Seconds 15
    $hc = Join-Path $ProjectRoot "scripts\deploy\healthcheck.ps1"
    if (Test-Path $hc) { & $hc -ProjectRoot $ProjectRoot }
}
