# Export business databases for Win10 offline bundle
# Usage:
#   .\scripts\deploy\export_databases.ps1 -OutputDir F:\antd-demo1\release\win10\SYFAXTYX_win10_20260707\data
#   .\scripts\deploy\export_databases.ps1 -OutputDir ... -SkipNeo4j -SkipMySql

param(
    [string]$ProjectRoot = "F:\antd-demo1",
    [string]$OutputDir = "",
    [string]$Neo4jHome = "",
    [string]$Neo4jAdminBin = "",
    [string]$MySqlBin = "",
    [string]$MySqlUser = "root",
    [string]$MySqlPassword = "",
    [string]$MySqlDatabase = "znwd_history",
    [string]$MinioDataDir = "",
    [switch]$SkipNeo4j,
    [switch]$SkipMySql,
    [switch]$SkipChroma,
    [switch]$SkipZvec,
    [switch]$SkipMinio
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_deploy_common.ps1")

if (-not $OutputDir) {
    $OutputDir = Join-Path $ProjectRoot "release\win10\data_export"
}
Ensure-Dir $OutputDir

Write-DeployStep "Export databases -> $OutputDir"

# --- Chroma ---
if (-not $SkipChroma) {
    $chromaSrc = Join-Path $ProjectRoot "chroma_data"
    $chromaDst = Join-Path $OutputDir "chroma_data"
    if (Test-Path $chromaSrc) {
        Write-Host "  chroma_data ..."
        Robo-Mirror $chromaSrc $chromaDst -Quiet
    } else {
        Write-Warning "  chroma_data not found: $chromaSrc"
    }
}

# --- Zvec ---
if (-not $SkipZvec) {
    $zvecSrc = Join-Path $ProjectRoot "backend\zvec_data"
    if (-not (Test-Path $zvecSrc)) {
        $zvecSrc = Join-Path $ProjectRoot "zvec_data"
    }
    $zvecDst = Join-Path $OutputDir "zvec_data"
    if (Test-Path $zvecSrc) {
        Write-Host "  zvec_data ..."
        Robo-Mirror $zvecSrc $zvecDst -Quiet
    } else {
        Write-Warning "  zvec_data not found"
    }
}

# --- MinIO data ---
if (-not $SkipMinio) {
    if (-not $MinioDataDir) { $MinioDataDir = "D:\MinIO\data" }
    $minioDst = Join-Path $OutputDir "minio_buckets"
    if (Test-Path $MinioDataDir) {
        Write-Host "  minio data ..."
        Robo-Mirror $MinioDataDir $minioDst -Quiet
    } else {
        Write-Warning "  MinIO data not found: $MinioDataDir"
    }
}

# --- MySQL dump ---
if (-not $SkipMySql) {
    $dumpFile = Join-Path $OutputDir "mysql_znwd_history.sql"
    $mysqldump = $null
    if ($MySqlBin) {
        $mysqldump = Join-Path $MySqlBin "mysqldump.exe"
    }
    if (-not $mysqldump -or -not (Test-Path $mysqldump)) {
        $mysqldump = (Get-Command mysqldump -ErrorAction SilentlyContinue).Source
    }
    if ($mysqldump -and (Test-Path $mysqldump)) {
        Write-Host "  mysqldump -> $dumpFile"
        $args = @("-u", $MySqlUser, "--single-transaction", "--routines", "--triggers", $MySqlDatabase)
        if ($MySqlPassword) { $args = @("-u", $MySqlUser, "-p$MySqlPassword") + $args[2..($args.Length-1)] }
        $env:MYSQL_PWD = $MySqlPassword
        & $mysqldump @args 2>&1 | Out-File -FilePath $dumpFile -Encoding utf8
        Remove-Item Env:MYSQL_PWD -ErrorAction SilentlyContinue
        if ((Get-Item $dumpFile).Length -lt 100) {
            Write-Warning "  mysqldump output very small — check MySQL credentials/service"
        }
    } else {
        Write-Warning "  mysqldump not found — skip MySQL export (copy sql init only)"
    }
    $initSql = Join-Path $ProjectRoot "backend\sql\createfa_history.sql"
    if (Test-Path $initSql) {
        Copy-Item $initSql (Join-Path $OutputDir "createfa_history.sql") -Force
    }
}

# --- Neo4j dump ---
if (-not $SkipNeo4j) {
    $neo4jAdmin = $Neo4jAdminBin
    if (-not $neo4jAdmin -and $Neo4jHome) {
        $neo4jAdmin = Join-Path $Neo4jHome "bin\neo4j-admin.bat"
    }
    if (-not $neo4jAdmin) {
        $neo4jAdmin = (Get-Command neo4j-admin -ErrorAction SilentlyContinue).Source
    }
    $dumpDir = Join-Path $OutputDir "neo4j.dump"
    Ensure-Dir $dumpDir
    if ($neo4jAdmin -and (Test-Path $neo4jAdmin)) {
        Write-Host "  neo4j-admin database dump ..."
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $neo4jAdmin database dump neo4j --to-path=$dumpDir 2>&1 | Write-Host
        $ErrorActionPreference = $prevEap
        $dumpFiles = Get-ChildItem $dumpDir -Filter "*.dump" -ErrorAction SilentlyContinue
        if ($dumpFiles.Count -eq 0) {
            Write-Warning "  neo4j dump failed — fallback: copy data directory"
            if ($Neo4jHome) {
                $dataSrc = Join-Path $Neo4jHome "data"
                if (Test-Path $dataSrc) {
                    Robo-Mirror $dataSrc (Join-Path $OutputDir "neo4j_data") -Quiet
                }
            }
        }
    } elseif ($Neo4jHome -and (Test-Path (Join-Path $Neo4jHome "data"))) {
        Write-Host "  neo4j-admin not found — copy data directory"
        Robo-Mirror (Join-Path $Neo4jHome "data") (Join-Path $OutputDir "neo4j_data") -Quiet
    } else {
        Write-Warning "  Neo4j not found — skip Neo4j export"
    }
}

Write-Host ""
Write-Host "Export complete: $OutputDir" -ForegroundColor Green
