# 5090 离线 wheel 打包（在有网机器执行，U 盘拷到 5090）
# 用法: .\scripts\mineru\download_offline_wheels.ps1 -OutDir D:\mineru_offline_wheels

param(
    [string]$OutDir = (Join-Path $PSScriptRoot "..\..\offline_wheels\mineru")
)

$ErrorActionPreference = "Stop"
$MineruEnv = if ($env:MINERU_ENV) { $env:MINERU_ENV } else { "agent" }
$CondaBase = if ($env:CONDA_BASE) { $env:CONDA_BASE } else { "C:\Users\hyx\anaconda3" }
$Python = Join-Path $CondaBase "envs\$MineruEnv\python.exe"

if (-not (Test-Path $Python)) {
    throw "找不到 Python: $Python"
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Write-Host "==> 下载 mineru[all]>=3.3.1 + torch (cu124) 到 $OutDir"
& $Python -m pip download -d $OutDir "mineru[all]>=3.3.1" "torch" --index-url https://download.pytorch.org/whl/cu124

Write-Host ""
Write-Host "完成。请连同 models/mineru/ 与 mineru.json 一并拷贝到 5090。" -ForegroundColor Green
Write-Host "5090 安装: pip install --no-index --find-links $OutDir mineru[all]"
