# Fix mineru.json UTF-8 BOM issue (MinerU json.load fails on BOM)
# Usage: .\scripts\mineru\fix_mineru_json_bom.ps1
#        .\scripts\mineru\fix_mineru_json_bom.ps1 -Path F:\antd-demo1\mineru.json

param(
    [string]$Path = ""
)

$ErrorActionPreference = "Stop"

if (-not $Path) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    if ($env:MINERU_TOOLS_CONFIG_JSON) {
        $Path = $env:MINERU_TOOLS_CONFIG_JSON
    } else {
        $Path = Join-Path $ProjectRoot "mineru.json"
    }
}

if (-not (Test-Path $Path)) {
    throw "mineru.json not found: $Path"
}

$bytes = [System.IO.File]::ReadAllBytes($Path)
if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    $raw = [System.Text.Encoding]::UTF8.GetString($bytes, 3, $bytes.Length - 3)
    Write-Host "Removed UTF-8 BOM from $Path"
} else {
    $raw = [System.Text.Encoding]::UTF8.GetString($bytes)
    Write-Host "No BOM detected, rewriting as UTF-8 no BOM: $Path"
}

$null = $raw.Trim() | ConvertFrom-Json

$enc = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($Path, $raw.Trim(), $enc)
Write-Host "Fixed: $Path" -ForegroundColor Green
