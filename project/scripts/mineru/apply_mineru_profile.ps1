# 将 MinerU profile 合并到项目根 .env
# 用法: .\scripts\mineru\apply_mineru_profile.ps1 -Profile local
#       .\scripts\mineru\apply_mineru_profile.ps1 -Profile rtx5090-wsl

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("local", "rtx5090", "rtx5090-windows", "rtx5090-wsl")]
    [string]$Profile,

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ProfileFile = Join-Path $ProjectRoot "config\mineru\$Profile.env"
$EnvFile = Join-Path $ProjectRoot ".env"

if (-not (Test-Path $ProfileFile)) {
    throw "Profile not found: $ProfileFile"
}

$profileLines = Get-Content $ProfileFile -Encoding UTF8 | Where-Object {
    $_ -notmatch '^\s*#' -and $_.Trim() -ne ''
}

$profileKeys = @{}
foreach ($line in $profileLines) {
    if ($line -match '^([^=]+)=(.*)$') {
        $profileKeys[$Matches[1].Trim()] = $Matches[2].Trim()
    }
}

$markerStart = "# ===== MinerU profile ($Profile) ====="
$markerEnd = "# ===== /MinerU profile ====="

$existing = @()
if (Test-Path $EnvFile) {
    $existing = Get-Content $EnvFile -Encoding UTF8
}

$out = [System.Collections.Generic.List[string]]::new()
$inBlock = $false
$skipped = $false

foreach ($line in $existing) {
    if ($line -eq $markerStart) {
        $inBlock = $true
        $skipped = $true
        continue
    }
    if ($inBlock) {
        if ($line -eq $markerEnd) {
            $inBlock = $false
        }
        continue
    }
    if (-not $inBlock) {
        $trimmed = $line.Trim()
        if ($trimmed -match '^([^=]+)=') {
            $key = $Matches[1].Trim()
            if ($profileKeys.ContainsKey($key)) {
                continue
            }
        }
        $out.Add($line)
    }
}

while ($out.Count -gt 0 -and [string]::IsNullOrWhiteSpace($out[$out.Count - 1])) {
    $out.RemoveAt($out.Count - 1)
}

$out.Add("")
$out.Add($markerStart)
$out.Add("# Applied: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') from config/mineru/$Profile.env")
foreach ($line in $profileLines) {
    $out.Add($line)
}
$out.Add($markerEnd)
$out.Add("")

$newContent = ($out -join "`r`n").TrimEnd() + "`r`n"

if ($DryRun) {
    Write-Host $newContent
    exit 0
}

Set-Content -Path $EnvFile -Value $newContent -Encoding UTF8
Write-Host "Applied profile '$Profile' to $EnvFile" -ForegroundColor Green
if ($Profile -in @("rtx5090-wsl")) {
    Write-Host "Next: .\scripts\mineru\start_mineru_wsl.ps1" -ForegroundColor Cyan
    Write-Host "First time in WSL: wsl bash /mnt/f/antd-demo1/scripts/mineru/wsl/setup_mineru_wsl.sh" -ForegroundColor Yellow
} else {
    Write-Host "Next: .\scripts\mineru\start_mineru_$Profile.ps1" -ForegroundColor Cyan
}
