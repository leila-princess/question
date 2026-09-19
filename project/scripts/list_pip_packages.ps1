# Export pip freeze from conda env (no EFHR dependency)
# Usage:
#   .\scripts\list_pip_packages.ps1
#   .\scripts\list_pip_packages.ps1 -ExportTxt F:\antd-demo1\offline_packages\requirements_offline.txt
#   .\scripts\list_pip_packages.ps1 -Days 40 -ExportTxt F:\antd-demo1\offline_packages\recent_pip_40d.txt

param(
    [int]$Days = 0,
    [string]$EnvName = "agent",
    [string]$CondaBase = "",
    [string]$ExportTxt = "",
    [string]$Keyword = ""
)

$ErrorActionPreference = "Stop"

if ($env:MINERU_ENV) { $EnvName = $env:MINERU_ENV }
if (-not $CondaBase) {
    $CondaBase = if ($env:CONDA_BASE) { $env:CONDA_BASE } else { "C:\Users\hyx\anaconda3" }
}

$python = Join-Path $CondaBase "envs\$EnvName\python.exe"
if (-not (Test-Path $python)) {
    throw "Python not found: $python"
}

function Export-FullFreeze {
    param([string]$OutPath)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $raw = & $python -m pip freeze 2>&1
    $ErrorActionPreference = $prevEap
    $lines = @($raw | Where-Object {
        ($_ -is [string]) -and ($_ -match '^[a-zA-Z0-9_\-\.\[]+==')
    })
    if ($lines.Count -eq 0) { throw "pip freeze returned no packages" }
    $header = @(
        "# pip freeze from env=$EnvName",
        "# generated=$(Get-Date -Format o)",
        ""
    )
    ($header + $lines) | Out-File -FilePath $OutPath -Encoding utf8
    Write-Host "wrote $($lines.Count) packages -> $OutPath"
    return $lines.Count
}

function Export-RecentPackages {
    param([string]$OutPath, [int]$DayCount)
    $sp = Join-Path $CondaBase "envs\$EnvName\Lib\site-packages"
    if (-not (Test-Path $sp)) { throw "site-packages not found: $sp" }

    $cutoff = (Get-Date).AddDays(-$DayCount)
    $rows = @()

    Get-ChildItem $sp -Filter "*.dist-info" -Directory | ForEach-Object {
        if ($_.LastWriteTime -lt $cutoff) { return }
        $metaPath = Join-Path $_.FullName "METADATA"
        $name = $_.Name -replace '\.dist-info$',''
        $pkgName = $name
        $version = ""
        if (Test-Path $metaPath) {
            $meta = Get-Content $metaPath -TotalCount 20 -Encoding UTF8
            $n = ($meta | Where-Object { $_ -match '^Name:\s*' }) -replace '^Name:\s*',''
            $v = ($meta | Where-Object { $_ -match '^Version:\s*' }) -replace '^Version:\s*',''
            if ($n) { $pkgName = $n }
            if ($v) { $version = $v }
        }
        if ($Keyword -and $pkgName -notmatch [regex]::Escape($Keyword) -and $name -notmatch [regex]::Escape($Keyword)) {
            return
        }
        $rows += [PSCustomObject]@{
            Package = $pkgName
            Version = $version
            Installed = $_.LastWriteTime
        }
    }

    $rows = $rows | Sort-Object Installed -Descending
    $seen = @{}
    $reqLines = @()
    foreach ($r in $rows) {
        $key = $r.Package.ToLower()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        if ($r.Version) { $reqLines += "$($r.Package)==$($r.Version)" }
        else { $reqLines += $r.Package }
    }

    $header = @(
        "# recent pip packages (last $DayCount days)",
        "# env=$EnvName",
        "# generated=$(Get-Date -Format o)",
        ""
    )
    ($header + $reqLines) | Out-File -FilePath $OutPath -Encoding utf8
    Write-Host "wrote $($reqLines.Count) packages -> $OutPath"
    return $reqLines.Count
}

if ($ExportTxt) {
    $dir = Split-Path $ExportTxt -Parent
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    if ($Days -gt 0) {
        Export-RecentPackages -OutPath $ExportTxt -DayCount $Days | Out-Null
    } else {
        Export-FullFreeze -OutPath $ExportTxt | Out-Null
    }
} else {
    if ($Days -gt 0) {
        $tmp = Join-Path $env:TEMP "pip_recent_$EnvName.txt"
        $count = Export-RecentPackages -OutPath $tmp -DayCount $Days
        Get-Content $tmp | Select-Object -First 30
        Write-Host "... ($count total, see $tmp)"
    } else {
        & $python -m pip freeze
    }
}
