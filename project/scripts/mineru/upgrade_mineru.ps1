# Upgrade MinerU to >= 3.3.1 (online). Offline: docs/MINERU_RTX5090_DEPLOY.md
# Usage: close mineru-api window first, then .\scripts\mineru\upgrade_mineru.ps1

$ErrorActionPreference = "Stop"
$MineruEnv = if ($env:MINERU_ENV) { $env:MINERU_ENV } else { "agent" }
$CondaBase = if ($env:CONDA_BASE) { $env:CONDA_BASE } else { "C:\Users\hyx\anaconda3" }
$Python = Join-Path $CondaBase "envs\$MineruEnv\python.exe"
$MineruExe = Join-Path $CondaBase "envs\$MineruEnv\Scripts\mineru.exe"

if (-not (Test-Path $Python)) {
    throw "Python not found: $Python (conda create -n $MineruEnv python=3.11)"
}

$ApiExe = Join-Path $CondaBase "envs\$MineruEnv\Scripts\mineru-api.exe"
if (Get-Process -Name "mineru-api" -ErrorAction SilentlyContinue) {
    Write-Host "WARN: mineru-api is running. Close its window before upgrade." -ForegroundColor Yellow
}

Write-Host "==> Current version:"
& $MineruExe --version 2>&1

Write-Host ""
Write-Host "==> 升级 mineru[all] >= 3.3.1 + CUDA PyTorch..."
& $Python -m pip install --upgrade pip
& $Python -m pip install -U "mineru[all]>=3.3.1" "torch" --index-url https://download.pytorch.org/whl/cu124

Write-Host ""
Write-Host "==> 升级后版本:"
& $MineruExe --version 2>&1
& $MineruExe --help 2>&1 | Select-String -Pattern "effort"

Write-Host ""
Write-Host "Done. Restart mineru-api window and Flask." -ForegroundColor Green
