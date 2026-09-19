# Install offline migration bundle on new machine (no network)
# Usage:
#   D:\0616\scripts\install_from_0616.ps1 -ProjectRoot F:\antd-demo1 -Source D:\0616

param(
    [string]$ProjectRoot = "F:\antd-demo1",
    [string]$Source = "",
    [string]$CondaBase = "",
    [string]$MineruEnv = "agent",
    [switch]$SkipPip,
    [switch]$SkipNodeModules
)

$ErrorActionPreference = "Stop"

if (-not $Source) {
    $Source = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $enc = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $Content, $enc)
}

function Robo-Mirror($src, $dst) {
    if (-not (Test-Path $src)) {
        Write-Host "  skip missing: $src" -ForegroundColor Yellow
        return
    }
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    robocopy $src $dst /E /XO /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed: $src -> $dst (exit $LASTEXITCODE)" }
}

Write-Host "========================================" -ForegroundColor Green
Write-Host " Offline install from 0616 bundle" -ForegroundColor Green
Write-Host " Source : $Source" -ForegroundColor Green
Write-Host " Target : $ProjectRoot" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green

if (-not (Test-Path $ProjectRoot)) {
    throw "Project root not found: $ProjectRoot`nCreate it or copy base project first."
}

# 1) project code
Write-Step "1/5 Overlay project code"
$projSrc = Join-Path $Source "project"
if (-not (Test-Path $projSrc)) { throw "Missing $projSrc" }
Robo-Mirror $projSrc $ProjectRoot

# 2) models
Write-Step "2/5 Copy MinerU models"
$modelSrc = Join-Path $Source "models\mineru"
$modelDst = Join-Path $ProjectRoot "models\mineru"
Robo-Mirror $modelSrc $modelDst

# 3) config
Write-Step "3/5 Copy mineru.json (UTF-8 no BOM)"
$configMineru = Join-Path $Source "config\mineru.json"
$targetMineru = Join-Path $ProjectRoot "mineru.json"
if (Test-Path $configMineru) {
    $jsonText = Get-Content $configMineru -Raw -Encoding UTF8
    Write-Utf8NoBom $targetMineru $jsonText
    Write-Host "  wrote $targetMineru"
} elseif (Test-Path (Join-Path $ProjectRoot "mineru.json.example")) {
    $example = Get-Content (Join-Path $ProjectRoot "mineru.json.example") -Raw -Encoding UTF8
    Write-Utf8NoBom $targetMineru $example
    Write-Host "  wrote $targetMineru from mineru.json.example"
}

$envSnippet = Join-Path $Source "config\env_offline_snippet.txt"
if (Test-Path $envSnippet) {
    Write-Host "  Review and merge: $envSnippet -> $ProjectRoot\.env" -ForegroundColor Yellow
}

# 4) pip wheels
if (-not $SkipPip) {
    Write-Step "4/5 pip install (offline)"
    if (-not $CondaBase) {
        if ($env:CONDA_BASE) { $CondaBase = $env:CONDA_BASE }
        else { $CondaBase = "C:\Users\hyx\anaconda3" }
    }
    $python = Join-Path $CondaBase "envs\$MineruEnv\python.exe"
    if (-not (Test-Path $python)) {
        Write-Warning "Python not found: $python - skip pip or fix CONDA_BASE"
    } else {
        $wheels = Join-Path $Source "packages\pip_wheels"
        $req = Join-Path $Source "packages\recent_pip_40d.txt"
        if ((Test-Path $wheels) -and (Test-Path $req)) {
            & $python -m pip install --no-index --find-links=$wheels -r $req
            Write-Host "  pip check:"
            & $python -m pip check
        } else {
            Write-Warning "  wheels or requirements missing under packages\"
        }
    }
} else {
    Write-Host "  skipped pip (-SkipPip)"
}

# 5) node_modules
if (-not $SkipNodeModules) {
    Write-Step "5/5 Copy fronted node_modules"
    $nmSrc = Join-Path $Source "fronted\node_modules"
    $nmDst = Join-Path $ProjectRoot "fronted\node_modules"
    Robo-Mirror $nmSrc $nmDst
    foreach ($f in @("package.json", "package-lock.json")) {
        $fs = Join-Path $Source "fronted\$f"
        if (Test-Path $fs) {
            Copy-Item $fs (Join-Path $ProjectRoot "fronted\$f") -Force
        }
    }
} else {
    Write-Host "  skipped node_modules (-SkipNodeModules)"
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " Install complete. Next steps:" -ForegroundColor Green
Write-Host "  1. Edit $ProjectRoot\.env (see config\env_offline_snippet.txt)" -ForegroundColor Yellow
Write-Host "  2. Edit $ProjectRoot\start-all.ps1 paths (Neo4j, MinIO, Conda)" -ForegroundColor Yellow
Write-Host "  3. cd $ProjectRoot; .\start-all.ps1" -ForegroundColor Yellow
Write-Host "  4. Verify: curl http://127.0.0.1:8001/health" -ForegroundColor Yellow
Write-Host "  5. Upload PDF in file management, test method extract" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Green
