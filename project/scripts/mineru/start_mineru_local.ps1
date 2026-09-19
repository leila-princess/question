# MinerU API — 本机开发（CPU / 弱 GPU，pipeline 后端）
# 用法: .\scripts\mineru\start_mineru_local.ps1
# 保持本窗口常开；模型只加载一次，后续解析比 CLI 冷启动快很多。

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

$MineruEnv = if ($env:MINERU_ENV) { $env:MINERU_ENV } else { "agent" }
$CondaBase = if ($env:CONDA_BASE) { $env:CONDA_BASE } else { "C:\Users\hyx\anaconda3" }
$MineruApi = Join-Path $CondaBase "envs\$MineruEnv\Scripts\mineru-api.exe"

if (-not (Test-Path $MineruApi)) {
    throw "mineru-api not found: $MineruApi`nInstall: conda activate $MineruEnv; pip install -U `"mineru[all]`""
}

# ---------- 本机：pipeline，不 preload VLM，省内存 ----------
if (-not $env:MINERU_BACKEND) { $env:MINERU_BACKEND = "pipeline" }
if (-not $env:MINERU_LANG) { $env:MINERU_LANG = "ch" }
if (-not $env:MINERU_MODEL_SOURCE) { $env:MINERU_MODEL_SOURCE = "local" }
if (-not $env:MINERU_METHOD) { $env:MINERU_METHOD = "txt" }
if (-not $env:MINERU_FORMULA) { $env:MINERU_FORMULA = "true" }
if (-not $env:MINERU_TABLE) { $env:MINERU_TABLE = "true" }
if (-not $env:MINERU_TIMEOUT) { $env:MINERU_TIMEOUT = "3600" }
if (-not $env:MINERU_PARSE_SERIAL) { $env:MINERU_PARSE_SERIAL = "1" }

if (-not $env:WJGL_UPLOAD_PREVIEW) { $env:WJGL_UPLOAD_PREVIEW = "fitz" }
if (-not $env:WJGL_PDF_PARSE) { $env:WJGL_PDF_PARSE = "mineru" }

$MineruJson = Join-Path $ProjectRoot "mineru.json"
if (Test-Path $MineruJson) {
    $env:MINERU_TOOLS_CONFIG_JSON = $MineruJson
}

function Test-PortFree([int]$Port) {
    -not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

$HostPort = if ($env:MINERU_API_PORT) { [int]$env:MINERU_API_PORT } else { 8001 }
if (-not (Test-PortFree $HostPort)) {
    foreach ($p in 8001, 8002, 8003, 8787) {
        if (Test-PortFree $p) { $HostPort = $p; break }
    }
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " MinerU API (local / pipeline profile)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Project : $ProjectRoot"
Write-Host "Env     : $MineruEnv"
Write-Host "Backend : $env:MINERU_BACKEND"
Write-Host "URL     : http://127.0.0.1:$HostPort"
Write-Host ""
Write-Host "Add to .env (or run apply_mineru_profile.ps1 -Profile local):" -ForegroundColor Yellow
Write-Host "  MINERU_API_URL=http://127.0.0.1:$HostPort"
Write-Host "  MINERU_API_PORT=$HostPort"
Write-Host "  WJGL_UPLOAD_PREVIEW=fitz"
Write-Host ""
Write-Host "Keep this window open. First PDF may take 2-5 min to load models." -ForegroundColor Green
Write-Host ""

& $MineruApi --host 127.0.0.1 --port $HostPort
