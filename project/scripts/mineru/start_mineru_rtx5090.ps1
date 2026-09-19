# MinerU API — RTX 5090 / 16GB VRAM 优化启动
# 用法: .\scripts\mineru\start_mineru_rtx5090.ps1
# 保持本窗口常开；模型只加载一次，后续解析接近桌面版速度。
#
# 离线新机若未拷贝 VLM 模型（models/mineru/vlm 为空），会自动回退 pipeline，不 preload VLM。

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

$MineruEnv = if ($env:MINERU_ENV) { $env:MINERU_ENV } else { "agent" }
$CondaBase = if ($env:CONDA_BASE) { $env:CONDA_BASE } else { "C:\Users\hyx\anaconda3" }
if (-not (Test-Path (Join-Path $CondaBase "envs\$MineruEnv\python.exe"))) {
    foreach ($base in @("D:\Soft\Anaconda3", "D:\Anaconda3", "C:\ProgramData\anaconda3")) {
        if (Test-Path (Join-Path $base "envs\$MineruEnv\python.exe")) {
            $CondaBase = $base
            break
        }
    }
}
$MineruApi = Join-Path $CondaBase "envs\$MineruEnv\Scripts\mineru-api.exe"

if (-not (Test-Path $MineruApi)) {
    throw "mineru-api not found: $MineruApi`nInstall: conda activate $MineruEnv; pip install -U `"mineru[all]`""
}

function Test-VlmModelsReady {
    param([string]$MineruJsonPath)
    if (-not (Test-Path $MineruJsonPath)) { return $false }
    try {
        $cfg = Get-Content $MineruJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $vlmDir = [string]$cfg.'models-dir'.vlm
        if (-not $vlmDir) { return $false }
        $vlmDir = $vlmDir -replace '/', '\'
        if (-not (Test-Path $vlmDir)) { return $false }
        $files = Get-ChildItem $vlmDir -Recurse -File -ErrorAction SilentlyContinue
        if (-not $files -or $files.Count -eq 0) { return $false }
        $hasConfig = $files | Where-Object { $_.Name -eq 'config.json' } | Select-Object -First 1
        return [bool]$hasConfig
    } catch {
        return $false
    }
}

function Test-PipelineModelsReady {
    param([string]$MineruJsonPath)
    if (-not (Test-Path $MineruJsonPath)) { return $false }
    try {
        $cfg = Get-Content $MineruJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $pipelineDir = [string]$cfg.'models-dir'.pipeline
        if (-not $pipelineDir) { return $false }
        $safetensors = Join-Path ($pipelineDir -replace '/', '\') "models\MFR\unimernet_hf_small_2503\model.safetensors"
        return (Test-Path $safetensors)
    } catch {
        return $false
    }
}

# ---------- GPU / 质量 / 速度（16GB 显存推荐）----------
$env:CUDA_VISIBLE_DEVICES = if ($env:CUDA_VISIBLE_DEVICES) { $env:CUDA_VISIBLE_DEVICES } else { "0" }
$env:TORCH_CUDA_ALLOC_CONF = "expandable_segments:True"

$MineruJson = Join-Path $ProjectRoot "mineru.json"
if (Test-Path $MineruJson) {
    $env:MINERU_TOOLS_CONFIG_JSON = $MineruJson
}

function Test-LmdeployReady {
    param([string]$PythonExe)
    if (-not $PythonExe -or -not (Test-Path $PythonExe)) { return $false }
    try {
        & $PythonExe -c "import lmdeploy" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

$MineruPython = Join-Path $CondaBase "envs\$MineruEnv\python.exe"
$lmdeployReady = Test-LmdeployReady -PythonExe $MineruPython
$vlmReady = Test-VlmModelsReady -MineruJsonPath $MineruJson
$pipelineReady = Test-PipelineModelsReady -MineruJsonPath $MineruJson
$isWindows = ($env:OS -eq "Windows_NT")

# Windows: lmdeploy turbomind 在 5090 上常失败；pytorch 后端又依赖 triton（Windows 无 pip 包）
# hybrid + VLM preload 在 Windows 原生环境不可用，统一回退 pipeline
if ($isWindows -and $env:MINERU_BACKEND -ne "pipeline") {
    if ($env:MINERU_BACKEND -eq "hybrid-auto-engine" -or -not $env:MINERU_BACKEND) {
        Write-Host "NOTE: Windows has no lmdeploy/triton for hybrid VLM." -ForegroundColor Yellow
        Write-Host "      Using MINERU_BACKEND=pipeline (GPU pipeline still works)." -ForegroundColor Yellow
        $env:MINERU_BACKEND = "pipeline"
    }
}

if (-not $env:MINERU_BACKEND) {
    if ($vlmReady -and $lmdeployReady -and -not $isWindows) {
        $env:MINERU_BACKEND = "hybrid-auto-engine"
    } else {
        $env:MINERU_BACKEND = "pipeline"
    }
}

# hybrid + VLM preload needs lmdeploy (Linux/WSL) or fails at startup
if ($env:MINERU_BACKEND -eq "hybrid-auto-engine" -and -not $lmdeployReady -and -not $isWindows) {
    Write-Host "NOTE: lmdeploy not installed. Using MINERU_BACKEND=pipeline." -ForegroundColor Yellow
    $env:MINERU_BACKEND = "pipeline"
}

# hybrid-auto-engine requires VLM weights; offline bundle usually has pipeline only
if ($env:MINERU_BACKEND -eq "hybrid-auto-engine" -and (-not $vlmReady -or $isWindows)) {
    if (-not $vlmReady) {
        Write-Host "WARNING: models/mineru/vlm missing or no config.json." -ForegroundColor Yellow
    }
    Write-Host "         Switching to MINERU_BACKEND=pipeline (no VLM preload)." -ForegroundColor Yellow
    $env:MINERU_BACKEND = "pipeline"
}

if (-not $pipelineReady) {
    Write-Host "WARNING: pipeline model.safetensors not found." -ForegroundColor Red
    Write-Host "         Check mineru.json pipeline path and models/mineru copy." -ForegroundColor Red
}

if (-not $env:MINERU_EFFORT) { $env:MINERU_EFFORT = "medium" }
if (-not $env:MINERU_LANG) { $env:MINERU_LANG = "ch" }
if (-not $env:MINERU_MODEL_SOURCE) { $env:MINERU_MODEL_SOURCE = "local" }
# Linux hybrid 才用 lmdeploy；Windows 见上方 pipeline 回退
if (-not $isWindows) {
    if (-not $env:MINERU_LMDEPLOY_BACKEND) { $env:MINERU_LMDEPLOY_BACKEND = "pytorch" }
    if (-not $env:MINERU_LMDEPLOY_DEVICE) { $env:MINERU_LMDEPLOY_DEVICE = "cuda" }
}
if (-not $env:MINERU_METHOD) { $env:MINERU_METHOD = "auto" }
if (-not $env:MINERU_FORMULA) { $env:MINERU_FORMULA = "true" }
if (-not $env:MINERU_TABLE) { $env:MINERU_TABLE = "true" }
if (-not $env:MINERU_IMAGE_ANALYSIS) { $env:MINERU_IMAGE_ANALYSIS = "false" }
if (-not $env:MINERU_TIMEOUT) { $env:MINERU_TIMEOUT = "3600" }
if (-not $env:MINERU_PARSE_SERIAL) { $env:MINERU_PARSE_SERIAL = "0" }
if (-not $env:MINERU_API_MAX_CONCURRENT_REQUESTS) { $env:MINERU_API_MAX_CONCURRENT_REQUESTS = "2" }

if (-not $env:WJGL_UPLOAD_PREVIEW) { $env:WJGL_UPLOAD_PREVIEW = "mineru" }
if (-not $env:WJGL_PDF_PARSE) { $env:WJGL_PDF_PARSE = "mineru" }

function Test-PortFree([int]$Port) {
    -not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

$HostPort = if ($env:MINERU_API_PORT) { [int]$env:MINERU_API_PORT } else { 8001 }
if (-not (Test-PortFree $HostPort)) {
    foreach ($p in 8001, 8002, 8003, 8787) {
        if (Test-PortFree $p) { $HostPort = $p; break }
    }
}

$enableVlmPreload = ($env:MINERU_BACKEND -eq "hybrid-auto-engine" -and $vlmReady -and $lmdeployReady)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " MinerU API (RTX 5090 profile)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Project      : $ProjectRoot"
Write-Host "Env          : $MineruEnv"
Write-Host "CondaBase    : $CondaBase"
Write-Host "Backend      : $env:MINERU_BACKEND"
Write-Host "Lmdeploy     : $env:MINERU_LMDEPLOY_BACKEND"
Write-Host "Lmdeploy OK  : $lmdeployReady"
Write-Host "VLM preload  : $enableVlmPreload"
Write-Host "Pipeline OK  : $pipelineReady"
Write-Host "URL          : http://127.0.0.1:$HostPort"
Write-Host ""
Write-Host "Add to .env:" -ForegroundColor Yellow
Write-Host "  MINERU_BACKEND=$($env:MINERU_BACKEND)"
Write-Host "  MINERU_API_URL=http://127.0.0.1:$HostPort"
Write-Host "  MINERU_API_PORT=$HostPort"
Write-Host ""
if (-not $vlmReady) {
    Write-Host "Offline machine: pipeline only. For hybrid-auto-engine copy VLM to models/mineru/vlm." -ForegroundColor Yellow
}
Write-Host "Keep this window open." -ForegroundColor Green
Write-Host ""

if ($enableVlmPreload) {
    & $MineruApi --host 127.0.0.1 --port $HostPort --enable-vlm-preload true
} else {
    & $MineruApi --host 127.0.0.1 --port $HostPort
}
