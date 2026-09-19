# Restore business databases from Win10 offline bundle
# Usage:
#   .\scripts\deploy\restore_databases.ps1 -DataDir D:\SYFAXTYX\data -ProjectRoot D:\SYFAXTYX\project

param(
    [string]$DataDir = "",
    [string]$ProjectRoot = "",
    [string]$InstallRoot = "",
    [string]$Neo4jHome = "",
    [string]$Neo4jAdminBin = "",
    [string]$MySqlBin = "",
    [string]$MySqlUser = "root",
    [string]$MySqlPassword = "root",
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

if (-not $DataDir) { throw "DataDir required" }
if (-not $ProjectRoot) {
    if ($InstallRoot) { $ProjectRoot = Join-Path $InstallRoot "project" }
    else { throw "ProjectRoot or InstallRoot required" }
}

Write-DeployStep "Restore databases from $DataDir"

# --- Chroma ---
if (-not $SkipChroma) {
    $chromaSrc = Join-Path $DataDir "chroma_data"
    if ($InstallRoot) {
        $chromaDst = Join-Path $InstallRoot "data\chroma_data"
    } else {
        $chromaDst = Join-Path (Split-Path $ProjectRoot -Parent) "data\chroma_data"
    }
    if (Test-Path $chromaSrc) {
        Write-Host "  restore chroma_data -> $chromaDst"
        Robo-Mirror $chromaSrc $chromaDst -Quiet
    }
}

# --- Zvec ---
if (-not $SkipZvec) {
    $zvecSrc = Join-Path $DataDir "zvec_data"
    $zvecDst = Join-Path $ProjectRoot "backend\zvec_data"
    if (Test-Path $zvecSrc) {
        Write-Host "  restore zvec_data -> $zvecDst"
        Robo-Mirror $zvecSrc $zvecDst -Quiet
    }
}

# --- MinIO ---
if (-not $SkipMinio) {
    $minioSrc = Join-Path $DataDir "minio_buckets"
    if (-not $MinioDataDir -and $InstallRoot) {
        $MinioDataDir = Join-Path $InstallRoot "runtime\minio\data"
    }
    if ((Test-Path $minioSrc) -and $MinioDataDir) {
        Write-Host "  restore minio -> $MinioDataDir"
        Ensure-Dir $MinioDataDir
        Robo-Mirror $minioSrc $MinioDataDir -Quiet
    }
}

# --- MySQL ---
if (-not $SkipMySql) {
    $dumpFile = Join-Path $DataDir "mysql_znwd_history.sql"
    $mysql = $null
    if ($MySqlBin) { $mysql = Join-Path $MySqlBin "mysql.exe" }
    if (-not $mysql -or -not (Test-Path $mysql)) {
        $mysql = (Get-Command mysql -ErrorAction SilentlyContinue).Source
    }
    if ($mysql -and (Test-Path $mysql) -and (Test-Path $dumpFile)) {
        Write-Host "  mysql import $MySqlDatabase ..."
        $createDb = "CREATE DATABASE IF NOT EXISTS ``$MySqlDatabase`` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
        $env:MYSQL_PWD = $MySqlPassword
        & $mysql -u $MySqlUser -e $createDb 2>&1 | Out-Null
        Get-Content $dumpFile -Raw | & $mysql -u $MySqlUser $MySqlDatabase 2>&1 | Out-Null
        Remove-Item Env:MYSQL_PWD -ErrorAction SilentlyContinue
    } elseif (Test-Path (Join-Path $DataDir "createfa_history.sql")) {
        Write-Warning "  only init SQL available — import createfa_history.sql manually after MySQL starts"
    }
}

# --- Neo4j ---
if (-not $SkipNeo4j) {
    $neo4jAdmin = $Neo4jAdminBin
    if (-not $neo4jAdmin -and $Neo4jHome) {
        $neo4jAdmin = Join-Path $Neo4jHome "bin\neo4j-admin.bat"
    }
    $dumpDir = Join-Path $DataDir "neo4j.dump"
    $dumpFiles = Get-ChildItem $dumpDir -Filter "*.dump" -ErrorAction SilentlyContinue
    if ($neo4jAdmin -and (Test-Path $neo4jAdmin) -and $dumpFiles.Count -gt 0) {
        Write-Host "  neo4j-admin database load ..."
        $ErrorActionPreference = "Continue"
        foreach ($df in $dumpFiles) {
            & $neo4jAdmin database load neo4j --from-path=$($df.FullName) --overwrite-destination=true 2>&1 | Write-Host
        }
        $ErrorActionPreference = "Stop"
    } elseif (Test-Path (Join-Path $DataDir "neo4j_data")) {
        if (-not $Neo4jHome) { throw "Neo4jHome required to restore neo4j_data copy" }
        $neo4jDataDst = Join-Path $Neo4jHome "data"
        Write-Host "  restore neo4j data -> $neo4jDataDst"
        Robo-Mirror (Join-Path $DataDir "neo4j_data") $neo4jDataDst -Quiet
    }
}

Write-Host ""
Write-Host "Restore complete." -ForegroundColor Green
