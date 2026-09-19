# Download Linux wheels for offline WSL MinerU (run on ONLINE machine with WSL)
# Usage:
#   cd F:\antd-demo1
#   .\scripts\mineru\wsl\pack_wsl_offline.ps1
#
# Copies to: F:\antd-demo1\0616\packages\pip_wheels_linux\
# Also downloads Miniconda Linux installer if missing.

param(
    [string]$ProjectRoot = "",
    [string]$WslDistro = "",
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}
if (-not $OutDir) {
    $OutDir = Join-Path $ProjectRoot "0616\packages\pip_wheels_linux"
}

function Convert-ToWslPath([string]$WinPath) {
    $p = $WinPath.Trim().TrimEnd('\').Replace('\', '/')
    if ($p -match '^([A-Za-z]):(/.*|$)') {
        return "/mnt/$($Matches[1].ToLower())$($Matches[2])"
    }
    return $p
}

$wslProject = Convert-ToWslPath $ProjectRoot

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$distro = if ($WslDistro) { $WslDistro } else { "Ubuntu" }
$dlScript = "$wslProject/scripts/mineru/wsl/download_wsl_wheels.sh"
$wheelsWsl = Convert-ToWslPath $OutDir

Write-Host "==> Download Linux wheels via WSL ($distro)..." -ForegroundColor Cyan
Write-Host "    Output: $OutDir"
Write-Host "    Downloads cp310 Linux wheels (conda agent env); needs python3 + ~12GB free" -ForegroundColor DarkGray

wsl -d $distro bash -lc "WHEELS_OUT='$wheelsWsl' bash '$dlScript'"
if ($LASTEXITCODE -ne 0) { throw "download_wsl_wheels failed" }

$minicondaSh = Join-Path (Split-Path $OutDir) "Miniconda3-latest-Linux-x86_64.sh"
if (-not (Test-Path $minicondaSh)) {
    Write-Host "==> Download Miniconda Linux installer (for offline WSL)..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh" `
        -OutFile $minicondaSh -UseBasicParsing
}

Write-Host ""
Write-Host "Done. Copy to new PC:" -ForegroundColor Green
Write-Host "  0616\packages\pip_wheels_linux\"
Write-Host "  0616\packages\Miniconda3-latest-Linux-x86_64.sh"
Write-Host ""
Write-Host "New PC offline WSL install:" -ForegroundColor Yellow
Write-Host @"
  wsl bash -lc "
    export WHEELS_DIR=/mnt/f/antd-demo1/0616/packages/pip_wheels_linux
    export MINICONDA_SH=/mnt/f/antd-demo1/0616/packages/Miniconda3-latest-Linux-x86_64.sh
    bash /mnt/f/antd-demo1/scripts/mineru/wsl/setup_mineru_wsl.sh
  "
"@
