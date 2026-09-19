# Start MinerU API inside WSL (hybrid + VLM on Linux; avoids Windows triton issue)
# Usage:
#   .\scripts\mineru\start_mineru_wsl.ps1
#   .\scripts\mineru\start_mineru_wsl.ps1 -Profile pipeline-wsl
#   .\scripts\mineru\start_mineru_wsl.ps1 -Foreground

param(
    [ValidateSet("rtx5090-wsl", "pipeline-wsl", "hybrid")]
    [string]$Profile = "pipeline-wsl",
    [string]$WslDistro = "",
    [string]$ProjectRoot = "",
    [int]$Port = 0,
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

function Convert-ToWslPath([string]$WinPath) {
    if (-not $WinPath) { return "" }
    $p = $WinPath.Trim().TrimEnd('\').Replace('\', '/')
    if ($p -match '^([A-Za-z]):(/.*|$)') {
        $drive = $Matches[1].ToLower()
        $rest = $Matches[2]
        if (-not $rest) { $rest = "" }
        return "/mnt/$drive$rest"
    }
    return $p
}

function Resolve-WslDistro {
    param([string]$Preferred)
    $list = @(wsl -l -q 2>$null | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($Preferred -and ($list -contains $Preferred)) { return $Preferred }
    foreach ($name in @("Ubuntu", "Ubuntu-24.04", "Ubuntu-22.04")) {
        if ($list -contains $name) { return $name }
    }
    if ($list.Count -gt 0) { return $list[0] }
    throw "No WSL distro. Run: wsl --install -d Ubuntu"
}

try {
    wsl --status | Out-Null
} catch {
    throw "WSL not available. Run as admin: wsl --install -d Ubuntu"
}

$distro = Resolve-WslDistro -Preferred $WslDistro
$wslProject = Convert-ToWslPath $ProjectRoot
$wslScript = "$wslProject/scripts/mineru/wsl/mineru_api_wsl.sh"
$wslSetup = "$wslProject/scripts/mineru/wsl/setup_mineru_wsl.sh"
$hostPort = if ($Port -gt 0) { $Port } elseif ($env:MINERU_API_PORT) { [int]$env:MINERU_API_PORT } else { 8001 }

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " MinerU via WSL" -ForegroundColor Cyan
Write-Host " Distro   : $distro" -ForegroundColor Cyan
Write-Host " Profile  : $Profile" -ForegroundColor Cyan
Write-Host " Project  : $ProjectRoot" -ForegroundColor Cyan
Write-Host " API URL  : http://127.0.0.1:$hostPort" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$checkScript = @"
set -e
test -f '$wslScript' || { echo MISSING_SCRIPT; exit 2; }
if [ -f "`$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
  . "`$HOME/miniconda3/etc/profile.d/conda.sh"
elif [ -f "`$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
  . "`$HOME/anaconda3/etc/profile.d/conda.sh"
else
  echo NO_CONDA; exit 3
fi
conda activate agent 2>/dev/null || { echo NO_ENV; exit 4; }
command -v mineru-api >/dev/null || { echo NO_MINERU; exit 5; }
nvidia-smi >/dev/null 2>&1 && nvidia-smi --query-gpu=name --format=csv,noheader || echo NO_GPU
echo OK
"@

$checkResult = wsl -d $distro bash -lc $checkScript 2>&1 | Out-String
Write-Host $checkResult.Trim()

if ($checkResult -match 'MISSING_SCRIPT|NO_CONDA|NO_ENV|NO_MINERU') {
    Write-Host ""
    Write-Host "First-time setup (needs network in WSL):" -ForegroundColor Yellow
    Write-Host "  wsl -d $distro bash $wslSetup" -ForegroundColor Yellow
    throw "WSL MinerU not ready"
}
if ($checkResult -match 'NO_GPU') {
    Write-Host "WARN: GPU not visible in WSL — update Windows NVIDIA driver." -ForegroundColor Yellow
}

function Escape-BashSingleQuote([string]$Text) {
    if (-not $Text) { return "" }
    return $Text.Replace("'", "'\''")
}

# Write a one-shot bash launcher (avoid multiline bash in PowerShell -Command; export is not a PS cmdlet)
$launcherWin = Join-Path $env:TEMP ("mineru_wsl_launch_{0}.sh" -f $PID)
$launcherContent = @"
#!/usr/bin/env bash
set -euo pipefail
export PROJECT_ROOT='$(Escape-BashSingleQuote $wslProject)'
export MINERU_WSL_PROFILE='$(Escape-BashSingleQuote $Profile)'
export MINERU_API_PORT='$hostPort'
export MINERU_API_HOST='0.0.0.0'
exec bash '$(Escape-BashSingleQuote $wslScript)'
"@
[System.IO.File]::WriteAllText($launcherWin, ($launcherContent -replace "`r`n", "`n"), [System.Text.UTF8Encoding]::new($false))
$launcherWsl = Convert-ToWslPath $launcherWin

Write-Host ""
Write-Host "Windows .env: MINERU_API_URL=http://127.0.0.1:$hostPort" -ForegroundColor Yellow
Write-Host "Keep MinerU window open." -ForegroundColor Green
Write-Host ""

if ($Foreground) {
    wsl -d $distro bash $launcherWsl
} else {
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command",
        "wsl -d $distro bash $launcherWsl"
    )
    Write-Host "MinerU WSL started in new window." -ForegroundColor Green
}
