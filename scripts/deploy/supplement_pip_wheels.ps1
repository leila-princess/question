# Download pip wheels one-by-one; skip/log failures
# Usage:
#   .\scripts\deploy\supplement_pip_wheels.ps1 -WheelsDir F:\...\env\pip_wheels -RequirementsFile F:\...\env\requirements_offline_deduped.txt

param(
    [string]$WheelsDir = "",
    [string]$RequirementsFile = "",
    [string]$CondaBase = "C:\Users\hyx\anaconda3",
    [string]$MineruEnv = "agent",
    [string[]]$SkipPackages = @("awscli")
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "_deploy_common.ps1")

if (-not $WheelsDir -or -not $RequirementsFile) {
    throw "WheelsDir and RequirementsFile required"
}
$python = Join-Path $CondaBase "envs\$MineruEnv\python.exe"
if (-not (Test-Path $python)) { throw "Python not found: $python" }
Ensure-Dir $WheelsDir

$logOk = Join-Path (Split-Path $WheelsDir -Parent) "pip_download_ok.txt"
$logFail = Join-Path (Split-Path $WheelsDir -Parent) "pip_download_failed.txt"
$okLines = @()
$failLines = @()

$reqs = Get-Content $RequirementsFile -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $line
} | Where-Object { $_ }

Write-DeployStep "Supplement pip wheels ($($reqs.Count) packages) -> $WheelsDir"
$before = (Get-ChildItem $WheelsDir -File -EA SilentlyContinue | Measure-Object).Count
Write-Host "  existing wheels: $before"

$i = 0
foreach ($req in $reqs) {
    $i++
    $pkgName = if ($req -match "^([a-zA-Z0-9_\-\.]+)") { $Matches[1].ToLower() } else { $req }
    if ($SkipPackages -contains $pkgName) {
        Write-Host "  [$i/$($reqs.Count)] SKIP $req (excluded)" -ForegroundColor Yellow
        continue
    }
    if ($req -match ';') {
        $req = ($req -split ';')[0].Trim()
    }
    Write-Host "  [$i/$($reqs.Count)] $req ..." -NoNewline
    try {
        $out = & $python -m pip download -d $WheelsDir "$req" 2>&1
        $exit = $LASTEXITCODE
    } catch {
        $out = @($_.Exception.Message)
        $exit = 1
    }
    if ($exit -eq 0) {
        Write-Host " OK" -ForegroundColor Green
        $okLines += $req
    } else {
        $err = ($out | Where-Object { $_ -match "ERROR|No matching|Could not find" } | Select-Object -First 1)
        if (-not $err) { $err = ($out | Select-Object -Last 1) }
        Write-Host " FAIL" -ForegroundColor Red
        Write-Host "    $err" -ForegroundColor DarkRed
        $failLines += "$req | $err"
    }
}

$after = (Get-ChildItem $WheelsDir -File -EA SilentlyContinue | Measure-Object).Count
$okLines | Out-File $logOk -Encoding utf8
$failLines | Out-File $logFail -Encoding utf8

Write-Host ""
Write-Host "Done: $before -> $after wheels (+$($after - $before))" -ForegroundColor Green
Write-Host "  OK log   : $logOk ($($okLines.Count))" -ForegroundColor Green
Write-Host "  Fail log : $logFail ($($failLines.Count))" -ForegroundColor $(if ($failLines.Count) { "Yellow" } else { "Green" })
