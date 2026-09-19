# Export migration file manifest by LastWriteTime
# Usage:
#   .\scripts\export_migration_manifest.ps1
#   .\scripts\export_migration_manifest.ps1 -Days 30 -Output F:\antd-demo1\offline_packages\migration_prod_src_30d.txt

param(
    [int]$Days = 30,
    [string]$ProjectRoot = "F:\antd-demo1",
    [string]$Output = ""
)

$ext = '\.(py|tsx?|jsx?|ps1|md|json|example)$'
$exclude = '(\\node_modules\\|\\\.next\\|\\__pycache__\\|\\\.git\\|\\output\\|\\0616\\|\\\.pytest_cache\\|\\EFHR\\|\\chroma_data\\|\\data\\|\\backend\\logs\\|\\backend\\output_json\\|\\backend\\uploads\\|\\\.tsbuildinfo$|\\znwd-gitee\\|\\fronted\\public\\packages\\onlyoffice\\)'
$prodMatch = '(\\backend\\|\\fronted\\|\\scripts\\|\\docs\\|start-all|mineru\.json|\.env\.example|\\README\.md)'

if (-not $Output) {
    $Output = Join-Path $ProjectRoot "offline_packages\migration_prod_src_${Days}d.txt"
}

$cutoff = (Get-Date).AddDays(-$Days)
$rows = @()

Get-ChildItem $ProjectRoot -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.LastWriteTime -lt $cutoff) { return }
    if ($_.FullName -notmatch $ext) { return }
    if ($_.FullName -match $exclude) { return }
    if ($_.FullName -notmatch $prodMatch) { return }
    $rel = $_.FullName.Substring($ProjectRoot.Length + 1)
    $rows += [PSCustomObject]@{
        When = $_.LastWriteTime.ToString("yyyy-MM-dd HH:mm")
        Path = $rel
        Bytes = $_.Length
    }
}

$dir = Split-Path $Output -Parent
if ($dir -and -not (Test-Path $dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

$header = @(
    "# migration manifest (production source, last $Days days)",
    "# generated=$(Get-Date -Format o)",
    "# count=$($rows.Count)",
    ""
)
($header + ($rows | Sort-Object When -Descending | ForEach-Object { "$($_.When)`t$($_.Path)`t$($_.Bytes)" })) |
    Out-File -FilePath $Output -Encoding utf8

Write-Host "wrote $($rows.Count) entries -> $Output"
