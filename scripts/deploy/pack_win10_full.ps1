# Win10 full offline release pack (program + env + runtime + databases)
# Usage:
#   cd F:\antd-demo1
#   .\scripts\deploy\pack_win10_full.ps1
#   .\scripts\deploy\pack_win10_full.ps1 -OutputDir F:\release\SYFAXTYX_win10_20260707 -SkipBuild

param(
    [string]$ProjectRoot = "F:\antd-demo1",
    [string]$OutputDir = "",
    [string]$CondaBase = "",
    [string]$MineruEnv = "agent",
    [string]$Neo4jHome = "D:\soft\Neo4j\neo4j-community-2025.06.2-windows\neo4j-community-2025.06.2",
    [string]$MinioExe = "D:\MinIO\bin\minio.exe",
    [string]$MinioDataDir = "D:\MinIO\data",
    [string]$MySqlBin = "C:\Program Files\MySQL\MySQL Server 8.0\bin",
    [string]$MySqlPassword = "",
    [switch]$SkipBuild,
    [switch]$SkipProjectCopy,
    [switch]$SkipFrontedDeps,
    [switch]$SkipWheels,
    [switch]$SkipModels,
    [switch]$SkipData,
    [switch]$SkipRuntime,
    [switch]$SkipMiniconda,
    [switch]$SkipRedisPack
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_deploy_common.ps1")

if (-not $CondaBase) {
    $CondaBase = if ($env:CONDA_BASE) { $env:CONDA_BASE } else { "C:\Users\hyx\anaconda3" }
}
if (-not $OutputDir) {
    $dateTag = Get-Date -Format "yyyyMMdd"
    $OutputDir = Join-Path $ProjectRoot "release\win10\SYFAXTYX_win10_$dateTag"
}

$patternFile = Join-Path $ProjectRoot "release\exclude_patterns.txt"
$patterns = Load-ExcludePatterns $patternFile

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host " Win10 Full Offline Pack" -ForegroundColor Cyan
Write-Host " Output: $OutputDir" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan

if (Test-Path $OutputDir) {
    Write-Warning "OutputDir exists — files will be merged/overwritten"
} else {
    Ensure-Dir $OutputDir
}

# ========== Step 0: Frontend build gate ==========
Write-DeployStep "0/9 Frontend build verification"
$frontedDir = Join-Path $ProjectRoot "fronted"
$nextStandalone = Join-Path $frontedDir ".next\standalone\server.js"
$bundleNext = Join-Path $OutputDir "fronted_deps\.next\standalone\server.js"
if (-not $SkipBuild) {
    Push-Location $frontedDir
    try {
        if (-not (Test-Path "node_modules\.bin\next.cmd")) {
            Write-Host "  npm install ..."
            npm install --prefer-offline --no-audit --no-fund 2>&1 | Out-File (Join-Path $OutputDir "npm_install.log") -Encoding utf8
        }
        Write-Host "  npm run build ..."
        npm run build 2>&1 | Tee-Object -FilePath (Join-Path $OutputDir "npm_build.log")
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed — see npm_build.log" }
    } finally {
        Pop-Location
    }
}
if (-not (Test-Path $nextStandalone) -and -not (Test-Path $bundleNext)) {
    throw "Frontend build missing: $nextStandalone — run without -SkipBuild or fix build errors"
}
if (Test-Path $nextStandalone) {
    Write-Host "  OK: .next/standalone/server.js" -ForegroundColor Green
} else {
    Write-Host "  OK: using bundled fronted_deps/.next" -ForegroundColor Green
}

# ========== Step 1: Project code ==========
if (-not $SkipProjectCopy) {
Write-DeployStep "1/9 Copy production project code"
$projOut = Join-Path $OutputDir "project"
Ensure-Dir $projOut
$copyRoots = @("backend", "fronted", "scripts", "docs")
$rootFiles = @("README.md", "mineru.json.example")
$manifest = @()

foreach ($rel in $copyRoots) {
    $srcRoot = Join-Path $ProjectRoot $rel
    if (-not (Test-Path $srcRoot)) { continue }
    Write-Host "  $rel ..."
    Get-ChildItem $srcRoot -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
        $relPath = $_.FullName.Substring($ProjectRoot.Length).TrimStart('\', '/')
        if (Test-ExcludedPath $relPath $patterns) { return }
        # skip runtime dirs inside fronted
        if ($relPath -match '\\node_modules\\|\\\.next\\') { return }
        $dst = Join-Path $projOut $relPath
        $dstDir = Split-Path $dst -Parent
        Ensure-Dir $dstDir
        Copy-Item $_.FullName $dst -Force
        $manifest += [PSCustomObject]@{ Path = "project/$($relPath.Replace('\','/'))"; Bytes = $_.Length }
    }
}
foreach ($rf in $rootFiles) {
    $src = Join-Path $ProjectRoot $rf
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $projOut $rf) -Force
    }
}

# Copy router_mlp.pkl if exists (from EFHR experiment, not the whole EFHR dir)
$mlpCandidates = @(
    (Join-Path $ProjectRoot "backend\blueprints\GLCX\ZNWD4\models\router_mlp.pkl"),
    (Join-Path $ProjectRoot "EFHR\experiments\case_a\exp4_qwen25\models\router_mlp_exp4_qwen25.pkl")
)
foreach ($mlp in $mlpCandidates) {
    if (Test-Path $mlp) {
        $mlpDst = Join-Path $projOut "backend\blueprints\GLCX\ZNWD4\models\router_mlp.pkl"
        Ensure-Dir (Split-Path $mlpDst -Parent)
        Copy-Item $mlp $mlpDst -Force
        Write-Host "  included router_mlp.pkl" -ForegroundColor Green
        break
    }
}
} else {
    Write-Host "  skipped project copy (-SkipProjectCopy)"
}

# ========== Step 2: pip wheels ==========
Write-DeployStep "2/9 pip wheels + requirements"
$envDir = Join-Path $OutputDir "env"
Ensure-Dir $envDir
if (-not $SkipWheels) {
    $reqRecent = Join-Path $envDir "requirements_recent.txt"
    $listScript = Join-Path $ProjectRoot "scripts\list_pip_packages.ps1"
    & $listScript -Days 40 -ExportTxt $reqRecent -CondaBase $CondaBase -EnvName $MineruEnv

    # Merge: docker + redis + recent 40d (avoid full freeze dependency conflicts)
    $reqMerged = Join-Path $envDir "requirements_merged.txt"
    $mergeSources = @(
        (Join-Path $ProjectRoot "backend\requirements-docker.txt"),
        (Join-Path $ProjectRoot "backend\requirements-redis.txt"),
        $reqRecent
    )
    $mergeLines = @("# merged offline requirements", "# generated=$(Get-Date -Format o)", "")
    foreach ($src in $mergeSources) {
        if (-not (Test-Path $src)) { continue }
        Get-Content $src -Encoding UTF8 | ForEach-Object {
            $line = $_.Trim()
            if (-not $line -or $line.StartsWith("#")) { return }
            if ($line -match '^awscli==') { return }
            $mergeLines += $line
        }
    }
    $mergeLines | Out-File $reqMerged -Encoding utf8
    $reqDeduped = Join-Path $envDir "requirements_offline_deduped.txt"
    Dedupe-Requirements $reqMerged $reqDeduped | Out-Null
    $wheelsDir = Join-Path $envDir "pip_wheels"
    Ensure-Dir $wheelsDir

    # Reuse 0616 wheels if available
    $w0616 = Join-Path $ProjectRoot "0616\packages\pip_wheels"
    if (Test-Path $w0616) {
        Write-Host "  copy existing wheels from 0616 ..."
        Robo-Mirror $w0616 $wheelsDir -Quiet
    }

    $python = Join-Path $CondaBase "envs\$MineruEnv\python.exe"
    if (-not (Test-Path $python)) { throw "Python not found: $python" }
    Write-Host "  pip download missing wheels (may take several minutes) ..."
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $python -m pip download -r $reqDeduped -d $wheelsDir 2>&1 | Tee-Object (Join-Path $envDir "pip_download.log")
    $ErrorActionPreference = $prevEap
    Copy-Item $reqDeduped (Join-Path $envDir "requirements_offline.txt") -Force
    $wheelCount = (Get-ChildItem $wheelsDir -File -EA SilentlyContinue | Measure-Object).Count
    Write-Host "  wheels: $wheelCount files"
    if ($wheelCount -lt 20) {
        Write-Warning "  very few wheels downloaded — check pip_download.log"
    }
} else {
    Write-Host "  skipped wheels (-SkipWheels)"
}

# ========== Step 3: frontend deps ==========
if (-not $SkipFrontedDeps) {
Write-DeployStep "3/9 frontend node_modules + .next"
$fdOut = Join-Path $OutputDir "fronted_deps"
Ensure-Dir $fdOut
foreach ($f in @("package.json", "package-lock.json")) {
    Copy-Item (Join-Path $frontedDir $f) $fdOut -Force -ErrorAction SilentlyContinue
}
Robo-Mirror (Join-Path $frontedDir "node_modules") (Join-Path $fdOut "node_modules") -Quiet
Robo-Mirror (Join-Path $frontedDir ".next") (Join-Path $fdOut ".next") -Quiet
Robo-Mirror (Join-Path $frontedDir "public") (Join-Path $fdOut "public") -Quiet
} else {
    Write-Host "  skipped fronted deps (-SkipFrontedDeps)"
}

# ========== Step 4: models ==========
if (-not $SkipModels) {
    Write-DeployStep "4/9 models (MinerU + BGE)"
    $modelsOut = Join-Path $OutputDir "models"
    Ensure-Dir $modelsOut

    $pipelineProject = Join-Path $ProjectRoot "models\mineru\pipeline"
    $pipelineCache = Join-Path $env:USERPROFILE ".cache\modelscope\hub\models\OpenDataLab\PDF-Extract-Kit-1___0"
    $pipelineDst = Join-Path $modelsOut "mineru\pipeline"
    if ((Test-Path $pipelineProject) -and ((Get-ChildItem $pipelineProject -Recurse -File -EA SilentlyContinue | Measure-Object).Count -gt 0)) {
        Robo-Mirror $pipelineProject $pipelineDst -Quiet
    } elseif (Test-Path $pipelineCache) {
        Robo-Mirror $pipelineCache $pipelineDst -Quiet
    } else {
        Write-Warning "  MinerU pipeline model not found"
    }
    $vlmSrc = Join-Path $ProjectRoot "models\mineru\vlm"
    if (Test-Path $vlmSrc) { Robo-Mirror $vlmSrc (Join-Path $modelsOut "mineru\vlm") -Quiet }

    $bgeCandidates = @(
        (Join-Path $ProjectRoot "models\bge-small-zh-v1.5"),
        (Join-Path $ProjectRoot "ANZHUANGBAO\bge-small-zh-v1.5")
    )
    foreach ($bge in $bgeCandidates) {
        if (Test-Path $bge) {
            Robo-Mirror $bge (Join-Path $modelsOut "bge-small-zh-v1.5") -Quiet
            break
        }
    }
} else {
    Write-Host "  skipped models (-SkipModels)"
}

# ========== Step 5: databases ==========
if (-not $SkipData) {
    Write-DeployStep "5/9 export databases"
    $dataOut = Join-Path $OutputDir "data"
    & (Join-Path $PSScriptRoot "export_databases.ps1") -ProjectRoot $ProjectRoot -OutputDir $dataOut `
        -Neo4jHome $Neo4jHome -MySqlBin $MySqlBin -MySqlPassword $MySqlPassword -MinioDataDir $MinioDataDir
} else {
    Write-Host "  skipped data (-SkipData)"
}

# ========== Step 6: runtime services ==========
if (-not $SkipRuntime) {
    Write-DeployStep "6/9 collect runtime (Neo4j/MinIO/Redis/MySQL)"
    $rtOut = Join-Path $OutputDir "runtime"
    Ensure-Dir $rtOut

    if ($Neo4jHome -and (Test-Path $Neo4jHome)) {
        Write-Host "  neo4j ..."
        Robo-Mirror $Neo4jHome (Join-Path $rtOut "neo4j") -Quiet
    } else {
        Write-Warning "  Neo4jHome not found: $Neo4jHome"
    }

    $minioRt = Join-Path $rtOut "minio"
    Ensure-Dir $minioRt
    if ($MinioExe -and (Test-Path $MinioExe)) {
        Copy-Item $MinioExe (Join-Path $minioRt "minio.exe") -Force
    }
    if ($MinioDataDir -and (Test-Path $MinioDataDir)) {
        Robo-Mirror $MinioDataDir (Join-Path $minioRt "data") -Quiet
    }

    # Redis offline pack
    if (-not $SkipRedisPack) {
        $redisPackScript = Join-Path $ProjectRoot "scripts\redis\pack_redis_offline.ps1"
        $redisSrc = Join-Path $ProjectRoot "offline_packages\redis"
        if (Test-Path $redisPackScript) {
            $redisServerReady = Test-Path (Join-Path $redisSrc "server\redis-server.exe")
            if ($redisServerReady) {
                & $redisPackScript -ProjectRoot $ProjectRoot -CondaBase $CondaBase
            } else {
                & $redisPackScript -ProjectRoot $ProjectRoot -CondaBase $CondaBase -DownloadServer
            }
            if (Test-Path $redisSrc) {
                Robo-Mirror $redisSrc (Join-Path $rtOut "redis") -Quiet
            }
        }
    } elseif (Test-Path (Join-Path $ProjectRoot "offline_packages\redis")) {
        Robo-Mirror (Join-Path $ProjectRoot "offline_packages\redis") (Join-Path $rtOut "redis") -Quiet
    }

    # MySQL: copy bin + share if available (portable partial)
    if ($MySqlBin -and (Test-Path $MySqlBin)) {
        $mysqlHome = Split-Path $MySqlBin -Parent | Split-Path -Parent
        if (Test-Path $mysqlHome) {
            Write-Host "  mysql (from $mysqlHome) ..."
            Robo-Mirror $mysqlHome (Join-Path $rtOut "mysql") -Quiet
        }
    }
} else {
    Write-Host "  skipped runtime (-SkipRuntime)"
}

# ========== Step 7: Miniconda installer ==========
if (-not $SkipMiniconda) {
    Write-DeployStep "7/9 Miniconda installer"
    $condaExe = Join-Path $envDir "Miniconda3-latest-Windows-x86_64.exe"
    if (-not (Test-Path $condaExe)) {
        $url = "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"
        $bundledConda = Get-ChildItem $ProjectRoot -Recurse -Filter "Miniconda3-latest-Windows-x86_64.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($bundledConda) {
            Write-Host "  copy Miniconda from $($bundledConda.FullName) ..."
            Copy-Item $bundledConda.FullName $condaExe -Force
        } else {
            Write-Host "  download $url ..."
            Invoke-WebRequest -Uri $url -OutFile $condaExe -UseBasicParsing
        }
        if (-not (Test-Path $condaExe)) { throw "Miniconda installer not available" }
    } else {
        Write-Host "  reusing existing Miniconda installer"
    }
}

# ========== Step 8: config templates + install script ==========
Write-DeployStep "8/9 config templates + install script"
$configDir = Join-Path $OutputDir "config"
Ensure-Dir $configDir

$installConfig = @{
    InstallRoot = "{InstallRoot}"
    ProjectRoot = "{InstallRoot}/project"
    CondaBase = "{InstallRoot}/runtime/miniconda3"
    MineruEnv = $MineruEnv
    Neo4jHome = "{InstallRoot}/runtime/neo4j"
    MinioExe = "{InstallRoot}/runtime/minio/minio.exe"
    MinioDataDir = "{InstallRoot}/runtime/minio/data"
    MinioConsolePort = "9001"
    MySqlBin = "{InstallRoot}/runtime/mysql/bin"
    MySqlDataDir = "{InstallRoot}/runtime/mysql/data"
    MineruProfile = "local"
    ChromaPath = "{InstallRoot}/data/chroma_data"
    ZvecDataPath = "{InstallRoot}/project/backend/zvec_data"
}
$installConfig | ConvertTo-Json -Depth 5 | Out-File (Join-Path $configDir "install_config.template.json") -Encoding utf8

# Active config with placeholder for pack-time reference
$installConfigActive = $installConfig.Clone()
$installConfigActive.InstallRoot = $OutputDir
$installConfigActive.ProjectRoot = Join-Path $OutputDir "project"
$installConfigActive.CondaBase = Join-Path $OutputDir "runtime\miniconda3"
$installConfigActive.Neo4jHome = Join-Path $OutputDir "runtime\neo4j"
$installConfigActive.MinioExe = Join-Path $OutputDir "runtime\minio\minio.exe"
$installConfigActive.MinioDataDir = Join-Path $OutputDir "runtime\minio\data"
$installConfigActive.MySqlBin = Join-Path $OutputDir "runtime\mysql\bin"
$installConfigActive.MySqlDataDir = Join-Path $OutputDir "runtime\mysql\data"
$installConfigActive.ChromaPath = Join-Path $OutputDir "data\chroma_data"
$installConfigActive.ZvecDataPath = Join-Path $OutputDir "project\backend\zvec_data"
$installConfigActive | ConvertTo-Json -Depth 5 | Out-File (Join-Path $configDir "install_config.json") -Encoding utf8

$mineruJson = @{
    "_comment" = "offline bundle - paths rewritten on install"
    "models-dir" = @{
        pipeline = "{ProjectRoot}/models/mineru/pipeline"
        vlm      = "{ProjectRoot}/models/mineru/vlm"
    }
} | ConvertTo-Json -Depth 3
Write-Utf8NoBom (Join-Path $configDir "mineru.json") $mineruJson

# .env.template from .env.example with offline overrides
$envExample = Join-Path $ProjectRoot ".env.example"
$envTemplate = @"
# Auto-generated offline .env template — install_and_start.ps1 fills {InstallRoot}
OPENAI_API_KEY=sk-your-api-key
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen-plus

NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=CHANGE_ME

DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=root
DB_NAME=znwd_history

CHROMA_PATH={InstallRoot}/data/chroma_data
ZVEC_DATA_PATH={ProjectRoot}/backend/zvec_data

REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

MINIO_ENDPOINT=127.0.0.1:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin

MINERU_API_URL=http://127.0.0.1:8001
MINERU_BACKEND=pipeline
MINERU_MODEL_SOURCE=local
MINERU_TOOLS_CONFIG_JSON={ProjectRoot}/mineru.json
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1

FLGL_TESTMETHOD_BGE_MODEL_PATH={ProjectRoot}/models/bge-small-zh-v1.5
FLGL_TESTMETHOD_BGE_LOCAL_FILES_ONLY=1

NEXT_PUBLIC_ZNWD_CHAT_STREAM=/api/znwd6/chat/stream
NEXT_PUBLIC_API_URL=http://127.0.0.1:5000

ZNWD4_ROUTING_MODE=probe_hybrid
ZNWD4_INSTANT_THINKING=1
ZNWD4_ANSWER_FEEDBACK=1
ZNWD4_ROUTE_LOCK_MODE=rules
ZNWD4_ANSWER_SYNTHESIS=template
ZNWD4_LLM_TIMEOUT=180
ZNWD6_LLM_BINDING=1

WJGL_PDF_PARSE=mineru
FLGL_PDF_BACKEND=mineru
"@
Write-Utf8NoBom (Join-Path $configDir ".env.template") $envTemplate

# Copy deploy scripts + root install_and_start.ps1
$deployOut = Join-Path $OutputDir "scripts\deploy"
Ensure-Dir $deployOut
Get-ChildItem $PSScriptRoot -Filter "*.ps1" | Copy-Item -Destination $deployOut -Force

# install_and_start at bundle root (adjusted paths)
$installSrc = Join-Path $PSScriptRoot "install_and_start.ps1"
$installDst = Join-Path $OutputDir "install_and_start.ps1"
Copy-Item $installSrc $installDst -Force

Copy-Item (Join-Path $ProjectRoot "release\win10\README_DEPLOY.md") (Join-Path $OutputDir "README_DEPLOY.md") -Force -ErrorAction SilentlyContinue

# ========== Step 9: MANIFEST ==========
Write-DeployStep "9/9 MANIFEST.csv"
$allFiles = Get-ChildItem $OutputDir -Recurse -File -ErrorAction SilentlyContinue
$manifestRows = $allFiles | ForEach-Object {
    [PSCustomObject]@{
        Path = $_.FullName.Substring($OutputDir.Length + 1).Replace('\', '/')
        Bytes = $_.Length
        When = $_.LastWriteTime.ToString("yyyy-MM-dd HH:mm")
    }
}
$manifestRows | Export-Csv (Join-Path $OutputDir "MANIFEST.csv") -NoTypeInformation -Encoding UTF8

$totalGB = [math]::Round(($allFiles | Measure-Object Length -Sum).Sum / 1GB, 2)
Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host " DONE: $OutputDir" -ForegroundColor Green
Write-Host " Size : $totalGB GB ($($allFiles.Count) files)" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Copy entire folder to USB/disk, then on offline Win10:" -ForegroundColor Yellow
Write-Host "  .\install_and_start.ps1 -InstallRoot <path>" -ForegroundColor Yellow
