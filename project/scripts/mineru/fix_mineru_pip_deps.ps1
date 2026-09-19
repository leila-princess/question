# Fix MinerU / doclayout-yolo / cnstd pip deps after torch cu128 install (offline-friendly)
# Usage:
#   conda activate agent
#   .\scripts\mineru\fix_mineru_pip_deps.ps1 -WheelsDir D:\0616\packages\pip_wheels
#   .\scripts\mineru\fix_mineru_pip_deps.ps1 -CondaBase D:\Soft\Anaconda3 -MineruEnv agent
#   .\scripts\mineru\fix_mineru_pip_deps.ps1 -PythonPath D:\Soft\Anaconda3\envs\agent\python.exe
#   .\scripts\mineru\fix_mineru_pip_deps.ps1 -Online

param(
    [string]$CondaBase = "",
    [string]$MineruEnv = "agent",
    [string]$PythonPath = "",
    [string]$WheelsDir = "",
    [switch]$Online
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Read-DotEnvValue {
    param([string]$Key)
    $envFile = Join-Path $ProjectRoot ".env"
    if (-not (Test-Path $envFile)) { return "" }
    foreach ($line in Get-Content $envFile -Encoding UTF8 -ErrorAction SilentlyContinue) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith("#")) { continue }
        if ($t -match "^\s*$([regex]::Escape($Key))\s*=\s*(.+?)\s*$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return ""
}

function Resolve-MineruPython {
    param(
        [string]$CondaBaseHint,
        [string]$EnvName,
        [string]$ExplicitPython
    )

    if ($ExplicitPython) {
        $p = $ExplicitPython.Trim().Trim('"')
        if (Test-Path $p) {
            return @{ Python = (Resolve-Path $p).Path; CondaBase = $CondaBaseHint; Source = "-PythonPath" }
        }
    }

    if ($env:CONDA_PREFIX) {
        $active = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path $active) {
            $base = $env:CONDA_BASE
            if (-not $base) {
                if ($env:CONDA_PREFIX -match '^(.*)\\envs\\[^\\]+$') { $base = $Matches[1] }
            }
            return @{ Python = (Resolve-Path $active).Path; CondaBase = $base; Source = "CONDA_PREFIX ($env:CONDA_DEFAULT_ENV)" }
        }
    }

    $pathPython = Get-Command python -ErrorAction SilentlyContinue
    if ($pathPython -and $pathPython.Source -and (Test-Path $pathPython.Source)) {
        $src = $pathPython.Source
        if ($src -match '^(.*)\\envs\\([^\\]+)\\python.exe$') {
            return @{ Python = $src; CondaBase = $Matches[1]; Source = "PATH python ($($Matches[2]))" }
        }
        return @{ Python = $src; CondaBase = $CondaBaseHint; Source = "PATH python" }
    }

    $condaCmd = Get-Command conda -ErrorAction SilentlyContinue
    if ($condaCmd) {
        try {
            $detectedBase = (& conda info --base 2>$null | Select-Object -Last 1).ToString().Trim()
            if ($detectedBase -and (Test-Path $detectedBase)) {
                $CondaBaseHint = if ($CondaBaseHint) { $CondaBaseHint } else { $detectedBase }
            }
        } catch { }
    }

    $candidates = @()
    if ($CondaBaseHint) { $candidates += $CondaBaseHint }
    if ($env:CONDA_BASE) { $candidates += $env:CONDA_BASE }
    $candidates += (Read-DotEnvValue "CONDA_BASE")
    $candidates += @(
        "D:\Soft\Anaconda3",
        "D:\Anaconda3",
        "C:\ProgramData\anaconda3",
        "C:\ProgramData\miniconda3",
        "C:\Users\hyx\anaconda3",
        "$env:USERPROFILE\anaconda3",
        "$env:USERPROFILE\miniconda3",
        "$env:LOCALAPPDATA\anaconda3",
        "$env:LOCALAPPDATA\miniconda3"
    )
    $candidates = $candidates | Where-Object { $_ } | Select-Object -Unique

    foreach ($base in $candidates) {
        $p = Join-Path $base "envs\$EnvName\python.exe"
        if (Test-Path $p) {
            return @{ Python = (Resolve-Path $p).Path; CondaBase = $base; Source = "envs\$EnvName under $base" }
        }
    }

    return $null
}

function Invoke-PipInstall {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Packages
    )
    if (-not $Packages -or $Packages.Count -eq 0) {
        throw "Invoke-PipInstall: empty package list"
    }

    Write-Host "  pip install $($Packages -join ' ')" -ForegroundColor DarkGray

    if ($Online) {
        & $Python -m pip install @Packages
    } elseif ($WheelsDir -and (Test-Path $WheelsDir)) {
        & $Python -m pip install --no-index --find-links="$WheelsDir" @Packages
    } else {
        & $Python -m pip install @Packages
    }
    if ($LASTEXITCODE -ne 0) {
        throw "pip install failed (exit $LASTEXITCODE). Missing wheels? Try: .\scripts\mineru\fix_mineru_pip_deps.ps1 -Online"
    }
}

function Test-PipShow {
    param([string]$Name)
    & $Python -m pip show $Name 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

if (-not $CondaBase) {
    $CondaBase = Read-DotEnvValue "CONDA_BASE"
    if (-not $CondaBase -and $env:CONDA_BASE) { $CondaBase = $env:CONDA_BASE }
}

$envNameFromDotEnv = Read-DotEnvValue "MINERU_ENV"
if ($envNameFromDotEnv) { $MineruEnv = $envNameFromDotEnv }

$resolved = Resolve-MineruPython -CondaBaseHint $CondaBase -EnvName $MineruEnv -ExplicitPython $PythonPath
if (-not $resolved) {
    $tried = @(
        "  1) conda activate $MineruEnv  后再运行本脚本",
        "  2) -CondaBase D:\Soft\Anaconda3  （改成你本机 Anaconda 根目录）",
        "  3) -PythonPath D:\Soft\Anaconda3\envs\agent\python.exe",
        "  4) 在 .env 里设置 CONDA_BASE=你的Anaconda根目录"
    ) -join "`n"
    throw @"
找不到 Python 环境 '$MineruEnv'。

请先执行其一：
$tried

若环境尚未创建：
  conda create -n $MineruEnv python=3.11 -y
"@
}

$Python = $resolved.Python
if ($resolved.CondaBase) { $CondaBase = $resolved.CondaBase }
$sitePackages = if ($env:CONDA_PREFIX -and $resolved.Source -like "CONDA_PREFIX*") {
    Join-Path $env:CONDA_PREFIX "Lib\site-packages"
} elseif ($CondaBase) {
    Join-Path $CondaBase "envs\$MineruEnv\Lib\site-packages"
} else {
    Join-Path (Split-Path (Split-Path $Python -Parent) -Parent) "Lib\site-packages"
}

if (-not $WheelsDir) {
    foreach ($c in @(
        (Join-Path $ProjectRoot "0616\packages\pip_wheels"),
        (Join-Path $ProjectRoot "offline_packages\pip_wheels"),
        "D:\0616\packages\pip_wheels"
    )) {
        if (Test-Path $c) { $WheelsDir = $c; break }
    }
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Fix MinerU pip dependencies" -ForegroundColor Cyan
Write-Host " Python : $Python" -ForegroundColor Cyan
Write-Host " Source : $($resolved.Source)" -ForegroundColor Cyan
Write-Host " Mode   : $(if ($Online) { 'online' } else { 'offline' })" -ForegroundColor Cyan
Write-Host " Wheels : $(if ($WheelsDir) { $WheelsDir } else { '(none)' })" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

if (-not $Online -and $WheelsDir) {
    $needPatterns = @("matplotlib", "seaborn", "thop", "ultralytics", "cnstd", "doclayout")
    $wheelNames = (Get-ChildItem $WheelsDir -Filter "*.whl" -ErrorAction SilentlyContinue | ForEach-Object { $_.Name.ToLower() })
    $missing = @()
    foreach ($pat in $needPatterns) {
        if (-not ($wheelNames | Where-Object { $_ -like "*$pat*" })) { $missing += $pat }
    }
    if ($missing.Count -gt 0) {
        Write-Host "WARNING: wheels 目录缺少: $($missing -join ', ')" -ForegroundColor Yellow
        Write-Host "  离线会失败。有网请加 -Online；无网请在有网机 pip download 后补拷 wheel。" -ForegroundColor Yellow
    }
}

if (Test-Path $sitePackages) {
    Get-ChildItem $sitePackages -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^[-~].*ineru' } |
        ForEach-Object {
            Write-Host "Remove broken dist: $($_.FullName)" -ForegroundColor Yellow
            Remove-Item $_.FullName -Recurse -Force
        }
}

Write-Host "`n==> upgrade pip / setuptools / wheel" -ForegroundColor Green
try {
    Invoke-PipInstall -Packages @("pip", "setuptools", "wheel")
} catch {
    Write-Host "  skip upgrade (offline wheels may not include pip): $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host "`n==> core deps for doclayout-yolo / cnstd / MinerU pipeline" -ForegroundColor Green
$core = @(
    "matplotlib>=3.3.0",
    "seaborn>=0.11.0",
    "thop>=0.1.1",
    "ultralytics>=8.0.0",
    "albumentations",
    "pandas",
    "scipy",
    "psutil",
    "py-cpuinfo",
    "opencv-python",
    "rapidocr",
    "onnxruntime",
    "pyclipper",
    "shapely",
    "pytorch-lightning",
    "onnx",
    "unidecode",
    "doclayout-yolo==0.0.4",
    "cnstd>=1.2.7",
    "mineru[all]==3.3.1"
)
Invoke-PipInstall -Packages $core

Write-Host "`n==> verify installed packages" -ForegroundColor Green
$required = @("matplotlib", "seaborn", "thop", "ultralytics", "doclayout_yolo", "cnstd", "mineru", "torch")
$notInstalled = @()
foreach ($pkg in $required) {
    if (Test-PipShow $pkg) {
        Write-Host "  OK  $pkg" -ForegroundColor Green
    } else {
        Write-Host "  MISSING  $pkg" -ForegroundColor Red
        $notInstalled += $pkg
    }
}
if ($notInstalled.Count -gt 0) {
    throw "以下包仍未安装: $($notInstalled -join ', ')。请使用 -Online 或补全 wheels 后重试。"
}

Write-Host "`n==> verify torch cuda" -ForegroundColor Green
& $Python -c "import torch; print('torch', torch.__version__); print('cuda', torch.cuda.is_available()); print('cuda_version', torch.version.cuda)"

Write-Host "`n==> verify imports" -ForegroundColor Green
& $Python -c @"
import matplotlib, seaborn, thop, ultralytics
import doclayout_yolo
import cnstd
import mineru
from mineru.model.mfr.unimernet.unimernet_hf import UnimernetModel
print('imports OK')
"@

Write-Host "`n==> pip check" -ForegroundColor Green
& $Python -m pip check

Write-Host "`n==> MinerU model path check" -ForegroundColor Green
$mineruJson = Read-DotEnvValue "MINERU_TOOLS_CONFIG_JSON"
if (-not $mineruJson) { $mineruJson = Join-Path $ProjectRoot "mineru.json" }
if (Test-Path $mineruJson) {
    $cfg = Get-Content $mineruJson -Raw -Encoding UTF8 | ConvertFrom-Json
    $pipeline = $cfg.'models-dir'.pipeline
    $safetensors = Join-Path $pipeline "models\MFR\unimernet_hf_small_2503\model.safetensors"
    Write-Host "  mineru.json pipeline: $pipeline"
    if (Test-Path $safetensors) {
        $mb = [math]::Round((Get-Item $safetensors).Length / 1MB, 1)
        Write-Host "  OK  model.safetensors (${mb} MB)" -ForegroundColor Green
    } else {
        Write-Host "  MISSING  $safetensors" -ForegroundColor Red
        Write-Host "  请 robocopy D:\0616\models\mineru 到项目 models\mineru，并修正 mineru.json 为本机路径" -ForegroundColor Yellow
    }
} else {
    Write-Host "  mineru.json not found: $mineruJson" -ForegroundColor Yellow
}

Write-Host "`nDone. Restart mineru-api and Flask." -ForegroundColor Green
Write-Host "若仍报 model architecture：.env 设 MINERU_BACKEND=pipeline，确认 mineru.json 指向本机 models\mineru\pipeline" -ForegroundColor Yellow
