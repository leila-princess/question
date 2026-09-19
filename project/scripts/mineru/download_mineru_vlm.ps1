# Download MinerU VLM model to project local directory (for offline copy to new PC)
# Usage:
#   cd F:\antd-demo1
#   .\scripts\mineru\download_mineru_vlm.ps1
#
# Output: models\mineru\vlm\  (robocopy to new PC)
# Also updates mineru.json "vlm" path (keeps existing pipeline path)

param(
    [string]$CondaBase = "",
    [string]$MineruEnv = "agent",
    [ValidateSet("modelscope", "huggingface")]
    [string]$Source = "modelscope"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$VlmDir = Join-Path $ProjectRoot "models\mineru\vlm"
$MineruJson = Join-Path $ProjectRoot "mineru.json"

if (-not $CondaBase) {
    if ($env:CONDA_BASE) { $CondaBase = $env:CONDA_BASE }
    else { $CondaBase = "C:\Users\hyx\anaconda3" }
}
$Python = Join-Path $CondaBase "envs\$MineruEnv\python.exe"
if (-not (Test-Path $Python)) {
    throw "Python not found: $Python"
}

New-Item -ItemType Directory -Force -Path $VlmDir | Out-Null

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Download MinerU VLM model" -ForegroundColor Cyan
Write-Host " Target : $VlmDir" -ForegroundColor Cyan
Write-Host " Source : $Source" -ForegroundColor Cyan
Write-Host " Model  : MinerU2.5-Pro-2605-1.2B (~several GB)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$env:HF_HUB_OFFLINE = "0"
$env:TRANSFORMERS_OFFLINE = "0"
$env:MINERU_MODEL_SOURCE = $Source
$env:MINERU_TOOLS_CONFIG_JSON = $MineruJson

Write-Host "`n==> ensure modelscope / huggingface_hub ..."
& $Python -m pip install -q --upgrade "modelscope>=1.20" "huggingface_hub>=0.23"

$pyScript = @"
import json
import os
import shutil

project_root = r"$ProjectRoot"
vlm_dir = r"$VlmDir"
source = r"$Source"
mineru_json = r"$MineruJson"

if source == "modelscope":
    from modelscope import snapshot_download
    repo = "OpenDataLab/MinerU2.5-Pro-2605-1.2B"
    print("Downloading from ModelScope:", repo)
    print("Local dir:", vlm_dir)
    cache = snapshot_download(repo, cache_dir=vlm_dir)
else:
    from huggingface_hub import snapshot_download
    repo = "opendatalab/MinerU2.5-Pro-2605-1.2B"
    print("Downloading from HuggingFace:", repo)
    cache = snapshot_download(repo, cache_dir=vlm_dir)

print("Download finished. cache_dir =", cache)

# modelscope may nest under hub/models/...; normalize to vlm_dir root if needed
config_path = None
for root, dirs, files in os.walk(vlm_dir):
    if "config.json" in files:
        config_path = os.path.join(root, "config.json")
        break

if not config_path:
    raise SystemExit("ERROR: config.json not found under " + vlm_dir)

model_root = os.path.dirname(config_path)
print("Model root with config.json:", model_root)

# If nested deeper than vlm_dir, copy up one level contents for simpler offline copy
try:
    rel = os.path.relpath(model_root, vlm_dir)
except ValueError:
    rel = "."

if rel not in (".", ""):
    print("Flattening nested model dir:", rel)
    nested = model_root
    tmp = vlm_dir + "_tmp_flat"
    if os.path.exists(tmp):
        shutil.rmtree(tmp)
    shutil.copytree(nested, tmp)
    shutil.rmtree(vlm_dir)
    os.rename(tmp, vlm_dir)
    model_root = vlm_dir

file_count = sum(len(files) for _, _, files in os.walk(vlm_dir))
total_bytes = sum(
    os.path.getsize(os.path.join(r, f))
    for r, _, files in os.walk(vlm_dir)
    for f in files
)
print(f"Files: {file_count}, Size GB: {total_bytes / (1024**3):.2f}")

pipeline = ""
if os.path.exists(mineru_json):
    with open(mineru_json, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    pipeline = (cfg.get("models-dir") or {}).get("pipeline") or ""

vlm_path = vlm_dir.replace("\\", "/")
new_cfg = {
    "models-dir": {
        "pipeline": pipeline or "F:/antd-demo1/models/mineru/pipeline",
        "vlm": vlm_path,
    }
}
with open(mineru_json, "w", encoding="utf-8") as f:
    json.dump(new_cfg, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("Updated mineru.json vlm ->", vlm_path)
print("DONE")
"@

$tmpPy = Join-Path $env:TEMP "download_mineru_vlm.py"
Set-Content -Path $tmpPy -Value $pyScript -Encoding UTF8
& $Python $tmpPy
if ($LASTEXITCODE -ne 0) { throw "VLM download failed (exit $LASTEXITCODE)" }

Write-Host ""
Write-Host "Copy to new PC:" -ForegroundColor Green
Write-Host "  robocopy `"$VlmDir`" F:\antd-demo1\models\mineru\vlm /E"
Write-Host "  mineru.json vlm path: F:/antd-demo1/models/mineru/vlm"
Write-Host "  .env: MINERU_BACKEND=hybrid-auto-engine  (optional, after VLM copied)"
