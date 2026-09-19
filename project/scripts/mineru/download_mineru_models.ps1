# MinerU model download to project local directory (run once on online machine)
# Usage:
#   cd F:\antd-demo1
#   .\scripts\mineru\download_mineru_models.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ModelsRoot = Join-Path $ProjectRoot "models\mineru"
$PipelineDir = Join-Path $ModelsRoot "pipeline"
$VlmDir = Join-Path $ModelsRoot "vlm"

New-Item -ItemType Directory -Force -Path $PipelineDir, $VlmDir | Out-Null

$CondaBase = if ($env:CONDA_BASE) { $env:CONDA_BASE } else { "C:\Users\hyx\anaconda3" }
$MineruEnv = if ($env:MINERU_ENV) { $env:MINERU_ENV } else { "agent" }
$Python = Join-Path $CondaBase "envs\$MineruEnv\python.exe"
$MineruDownload = Join-Path $CondaBase "envs\$MineruEnv\Scripts\mineru-models-download.exe"

if (-not (Test-Path $Python)) {
    throw "Python not found: $Python (create conda env $MineruEnv and pip install mineru)"
}

Write-Host "==> upgrade PyYAML / huggingface_hub..."
& $Python -m pip install --upgrade "PyYAML>=6.0" "huggingface_hub>=0.23"

$env:HF_HUB_OFFLINE = "0"
$env:TRANSFORMERS_OFFLINE = "0"
$env:MINERU_MODEL_SOURCE = "modelscope"

$MineruJson = Join-Path $ProjectRoot "mineru.json"
$mineruObj = @{
    "models-dir" = @{
        pipeline = ($PipelineDir -replace "\\", "/")
        vlm      = ($VlmDir -replace "\\", "/")
    }
}
$enc = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($MineruJson, ($mineruObj | ConvertTo-Json -Depth 3), $enc)
$env:MINERU_TOOLS_CONFIG_JSON = $MineruJson

Write-Host "==> download MinerU pipeline models..."
if (Test-Path $MineruDownload) {
    & $MineruDownload -s modelscope -m pipeline
} else {
    & $Python -m mineru.cli.models_download -s modelscope -m pipeline
}

Write-Host ""
Write-Host "Done. Set in .env for offline:"
Write-Host "MINERU_MODEL_SOURCE=local"
Write-Host "MINERU_TOOLS_CONFIG_JSON=$MineruJson"
