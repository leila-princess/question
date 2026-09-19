# Pack offline migration bundle into F:\antd-demo1\0616
# Usage: cd F:\antd-demo1; .\scripts\pack_0616.ps1
# Options: -SkipWheels -SkipModels -SkipNodeModules

param(
    [string]$ProjectRoot = "F:\antd-demo1",
    [string]$BundleRoot = "F:\antd-demo1\0616",
    [int]$SourceDays = 30,
    [switch]$SkipWheels,
    [switch]$SkipModels,
    [switch]$SkipNodeModules,
    [string]$CondaBase = "C:\Users\hyx\anaconda3",
    [string]$MineruEnv = "agent"
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $enc = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $Content, $enc)
}

function Ensure-Dir($path) {
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Force -Path $path | Out-Null
    }
}

function Robo-Copy($src, $dst, [string[]]$ExcludeDirs) {
    if (-not (Test-Path $src)) {
        Write-Host "  skip missing: $src" -ForegroundColor Yellow
        return
    }
    Ensure-Dir $dst
    $xd = ($ExcludeDirs | ForEach-Object { "/XD"; $_ })
    robocopy $src $dst /E /XO @xd /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed: $src (exit $LASTEXITCODE)" }
}

$manifestRows = @()
$logFile = Join-Path $BundleRoot "pack_log.txt"
Ensure-Dir $BundleRoot

Write-Step "Init bundle at $BundleRoot"

# --- config ---
Write-Step "config"
$configDir = Join-Path $BundleRoot "config"
Ensure-Dir $configDir

$offlineMineru = @{
    "_comment" = "offline bundle 0616 - copy to F:/antd-demo1/mineru.json"
    "models-dir" = @{
        pipeline = "F:/antd-demo1/models/mineru/pipeline"
        vlm      = "F:/antd-demo1/models/mineru/vlm"
    }
} | ConvertTo-Json -Depth 3
Write-Utf8NoBom (Join-Path $configDir "mineru.json") $offlineMineru

Copy-Item (Join-Path $ProjectRoot "mineru.json.example") (Join-Path $configDir "mineru.json.example") -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $ProjectRoot ".env.example") (Join-Path $configDir ".env.example") -Force

@"
# New machine .env required settings (merge into F:\antd-demo1\.env)

CONDA_BASE=C:\Users\<YOUR_USER>\anaconda3
MINERU_ENV=agent
MINERU_TOOLS_CONFIG_JSON=F:\antd-demo1\mineru.json
MINERU_MODEL_SOURCE=local
MINERU_API_URL=http://127.0.0.1:8001
MINERU_API_PORT=8001
MINERU_BACKEND=hybrid-auto-engine
MINERU_LANG=ch
MINERU_METHOD=auto
MINERU_FORMULA=true
MINERU_TABLE=true
MINERU_TIMEOUT=3600

HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1

WJGL_PDF_PARSE=mineru
FLGL_PDF_BACKEND=mineru
WJGL_UPLOAD_PREVIEW=mineru
WJGL_LOCAL_PARSE_ROOT=data/wjgl_documents

# OOM on 16GB VRAM:
# MINERU_BACKEND=pipeline
"@ | Out-File (Join-Path $configDir "env_offline_snippet.txt") -Encoding utf8

# --- project code ---
Write-Step "project code (backend + fronted + scripts + docs)"
$BundleProjectDir = Join-Path $BundleRoot "project"
Ensure-Dir $BundleProjectDir

$excludeDirs = @(
    "__pycache__", "node_modules", ".next", ".git", "0616", "0624", "06241", "06242", "0630", "release",
    "DTDL", "EFHR", "orio-search-master", "moniDB", ".opencode", "ANZHUANGBAO",
    "logs", "output_json", "uploads", ".pytest_cache", "output",
    "chroma_data", "data", "znwd-gitee", "packages"
)

$copyRoots = @(
    "backend", "fronted", "scripts", "docs"
)

foreach ($rel in $copyRoots) {
    $src = Join-Path $ProjectRoot $rel
    $dst = Join-Path $BundleProjectDir $rel
    Write-Host "  $rel ..."
    Robo-Copy $src $dst $excludeDirs
}

foreach ($rootFile in @("start-all.ps1", "README.md", "mineru.json.example", "retrain_znwd4_mlp.py", "20260617.txt")) {
    $src = Join-Path $ProjectRoot $rootFile
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $BundleProjectDir $rootFile) -Force
    }
}

# fronted: drop tsbuildinfo if copied
Get-ChildItem (Join-Path $BundleProjectDir "fronted") -Filter "tsconfig.tsbuildinfo" -Recurse -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

$totalFiles = (Get-ChildItem $BundleProjectDir -Recurse -File -EA SilentlyContinue | Measure-Object).Count
Write-Host "  project total: $totalFiles files"

# --- packages ---
Write-Step "pip packages list"
$pkgDir = Join-Path $BundleRoot "packages"
Ensure-Dir $pkgDir
$pipList = Join-Path $ProjectRoot "offline_packages\recent_pip_40d.txt"
if (-not (Test-Path $pipList)) {
    & (Join-Path $ProjectRoot "scripts\list_pip_packages.ps1") -Days 40 `
        -ExportTxt $pipList -CondaBase $CondaBase -EnvName $MineruEnv
}
Copy-Item $pipList (Join-Path $pkgDir "recent_pip_40d.txt") -Force

# dedupe requirements (same package may appear twice after upgrades)
$dedupedReq = Join-Path $pkgDir "recent_pip_40d_deduped.txt"
$seen = @{}
$reqLines = @()
Get-Content $pipList -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    if ($line -match "^([^=<>!\s]+)==") {
        $key = $Matches[1].ToLower()
        if (-not $seen.ContainsKey($key)) {
            $seen[$key] = $true
            $reqLines += $line
        }
    } else {
        $reqLines += $line
    }
}
$reqLines | Out-File $dedupedReq -Encoding utf8
$pipListForDownload = $dedupedReq

if (-not $SkipWheels) {
    Write-Step "pip download wheels (may take several minutes)"
    $wheelsDir = Join-Path $pkgDir "pip_wheels"
    Ensure-Dir $wheelsDir
    $python = Join-Path $CondaBase "envs\$MineruEnv\python.exe"
    if (-not (Test-Path $python)) { throw "Python not found: $python" }
    $errFile = Join-Path $pkgDir "download_errors.txt"
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $python -m pip download -r $pipListForDownload -d $wheelsDir 2>&1 | Tee-Object -FilePath $errFile
    $ErrorActionPreference = $prevEap
    $wheelCount = (Get-ChildItem $wheelsDir -File -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Host "  wheels: $wheelCount files"
} else {
    Write-Host "  skipped wheels (-SkipWheels)"
}

# --- models ---
if (-not $SkipModels) {
    Write-Step "MinerU models"
    $modelDst = Join-Path $BundleRoot "models\mineru"
    Ensure-Dir (Join-Path $modelDst "vlm")

    $pipelineProject = Join-Path $ProjectRoot "models\mineru\pipeline"
    $pipelineCache = "C:\Users\hyx\.cache\modelscope\hub\models\OpenDataLab\PDF-Extract-Kit-1___0"
    $pipelineDst = Join-Path $modelDst "pipeline"

    if ((Test-Path $pipelineProject) -and ((Get-ChildItem $pipelineProject -Recurse -File -EA SilentlyContinue | Measure-Object).Count -gt 0)) {
        Write-Host "  copy from project models\mineru\pipeline"
        robocopy $pipelineProject $pipelineDst /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
    } elseif (Test-Path $pipelineCache) {
        Write-Host "  copy from modelscope cache (~2.3GB)"
        robocopy $pipelineCache $pipelineDst /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
    } else {
        Write-Warning "  pipeline model not found - run download_mineru_models.ps1 first"
    }

    $vlmSrc = Join-Path $ProjectRoot "models\mineru\vlm"
    if (Test-Path $vlmSrc) {
        robocopy $vlmSrc (Join-Path $modelDst "vlm") /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
    }
} else {
    Write-Host "  skipped models (-SkipModels)"
}

# --- fronted node_modules ---
if (-not $SkipNodeModules) {
    Write-Step "fronted npm ci + node_modules"
    $frontedSrc = Join-Path $ProjectRoot "fronted"
    $frontedBundle = Join-Path $BundleRoot "fronted"
    Ensure-Dir $frontedBundle
    Copy-Item (Join-Path $frontedSrc "package.json") $frontedBundle -Force
    Copy-Item (Join-Path $frontedSrc "package-lock.json") $frontedBundle -Force -ErrorAction SilentlyContinue

    Push-Location $frontedSrc
    try {
        npm ci 2>&1 | Out-File (Join-Path $BundleRoot "npm_ci.log") -Encoding utf8
    } catch {
        Write-Host "  npm ci failed, will copy existing node_modules if present" -ForegroundColor Yellow
    } finally {
        Pop-Location
    }

    $nmDst = Join-Path $frontedBundle "node_modules"
    $nmSrc = Join-Path $frontedSrc "node_modules"
    if (Test-Path $nmSrc) {
        Write-Host "  robocopy node_modules..."
        robocopy $nmSrc $nmDst /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy node_modules failed (exit $LASTEXITCODE)" }
    } else {
        throw "fronted\node_modules not found - run npm ci in fronted first"
    }
} else {
    Write-Host "  skipped node_modules (-SkipNodeModules)"
}

# --- scripts into bundle ---
Write-Step "bundle scripts + docs"
$bundleScripts = Join-Path $BundleRoot "scripts"
Ensure-Dir $bundleScripts
Copy-Item (Join-Path $ProjectRoot "scripts\pack_0616.ps1") $bundleScripts -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $ProjectRoot "scripts\export_migration_manifest.ps1") $bundleScripts -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $ProjectRoot "scripts\install_from_0616.ps1") $bundleScripts -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $ProjectRoot "20260617.txt") (Join-Path $BundleRoot "20260617.txt") -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $ProjectRoot "0616\README_MIGRATION.md") (Join-Path $BundleRoot "README_MIGRATION.md") -Force -ErrorAction SilentlyContinue

# --- manifest ---
Write-Step "MANIFEST.csv"
$allFiles = Get-ChildItem $BundleRoot -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\0616\\0616\\' }
$manifest = $allFiles | ForEach-Object {
    [PSCustomObject]@{
        Path = $_.FullName.Substring($BundleRoot.Length + 1)
        Bytes = $_.Length
        When = $_.LastWriteTime.ToString("yyyy-MM-dd HH:mm")
    }
}
$manifest | Export-Csv (Join-Path $BundleRoot "MANIFEST.csv") -NoTypeInformation -Encoding UTF8

$totalGB = [math]::Round(($allFiles | Measure-Object Length -Sum).Sum / 1GB, 2)
Write-Host ""
Write-Host "DONE: $BundleRoot ($totalGB GB, $($allFiles.Count) files)" -ForegroundColor Green
Write-Host "Copy entire 0616 folder to USB, then run install_from_0616.ps1 on new PC." -ForegroundColor Yellow
