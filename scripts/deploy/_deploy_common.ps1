# Shared helpers for Win10 offline deploy scripts
# Dot-source: . (Join-Path $PSScriptRoot "_deploy_common.ps1")

$ErrorActionPreference = "Stop"

function Write-DeployStep([string]$msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $enc = New-Object System.Text.UTF8Encoding $false
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Content, $enc)
}

function Ensure-Dir([string]$path) {
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Force -Path $path | Out-Null
    }
}

function Robo-Mirror([string]$src, [string]$dst, [switch]$Quiet) {
    if (-not (Test-Path $src)) {
        Write-Host "  skip missing: $src" -ForegroundColor Yellow
        return $false
    }
    Ensure-Dir $dst
    if ($Quiet) {
        robocopy $src $dst /E /XO /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
    } else {
        robocopy $src $dst /E /XO /NFL /NDL /NJH /NJS | Out-Null
    }
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed: $src -> $dst (exit $LASTEXITCODE)" }
    return $true
}

function Load-ExcludePatterns([string]$PatternFile) {
    $dirs = @()
    $subs = @()
    $globs = @()
    if (-not (Test-Path $PatternFile)) { return @{ Dirs = $dirs; Subs = $subs; Globs = $globs } }
    Get-Content $PatternFile -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        if ($line -match '^DIR:(.+)$') { $dirs += $Matches[1].Trim() }
        elseif ($line -match '^SUB:(.+)$') { $subs += $Matches[1].Trim() }
        elseif ($line -match '^GLOB:(.+)$') { $globs += $Matches[1].Trim() }
    }
    return @{ Dirs = $dirs; Subs = $subs; Globs = $globs }
}

function Test-ExcludedPath([string]$relPath, $patterns) {
    $p = $relPath.Replace('/', '\')
    $parts = $p -split '[\\/]'
    foreach ($part in $parts) {
        foreach ($d in $patterns.Dirs) {
            if ($part -eq $d) { return $true }
        }
    }
    $lower = $p.ToLowerInvariant()
    foreach ($sub in $patterns.Subs) {
        if ($lower.Contains($sub.ToLowerInvariant())) { return $true }
    }
    $leaf = Split-Path $p -Leaf
    foreach ($glob in $patterns.Globs) {
        if ($leaf -like $glob) { return $true }
    }
    return $false
}

function Dedupe-Requirements([string]$InputFile, [string]$OutputFile) {
    $seen = @{}
    $reqLines = @()
    Get-Content $InputFile -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        if ($line -match "^([^=<>!\s]+)==") {
            $key = $Matches[1].ToLower()
            if (-not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                $reqLines += $line
            }
        } else {
            $reqLines += $line
        }
    }
    $reqLines | Out-File $OutputFile -Encoding utf8
    return $reqLines.Count
}

function Get-FreeSpaceGB([string]$Path) {
    $root = [System.IO.Path]::GetPathRoot((Resolve-Path $Path).Path)
    $drive = Get-PSDrive -Name ($root.TrimEnd('\')).TrimEnd(':') -ErrorAction SilentlyContinue
    if ($drive) { return [math]::Round($drive.Free / 1GB, 2) }
    return 0
}

function Normalize-WinPath([string]$path) {
    return ($path -replace '\\', '/')
}

function Expand-Template([string]$text, [hashtable]$vars) {
    $result = $text
    foreach ($key in $vars.Keys) {
        $result = $result.Replace("{$key}", [string]$vars[$key])
    }
    return $result
}
