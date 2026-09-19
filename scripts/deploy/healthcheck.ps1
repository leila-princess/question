# Health check for deployed Win10 offline stack
# Usage:
#   .\scripts\deploy\healthcheck.ps1
#   .\scripts\deploy\healthcheck.ps1 -ProjectRoot D:\SYFAXTYX\project

param(
    [string]$ProjectRoot = "F:\antd-demo1",
    [int]$TimeoutSec = 3
)

$ErrorActionPreference = "Continue"

function Test-TcpPort([string]$HostName, [int]$Port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne($TimeoutSec * 1000, $false)
        if ($ok -and $client.Connected) {
            $client.Close()
            return $true
        }
        $client.Close()
    } catch {}
    return $false
}

function Test-HttpOk([string]$Url) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400)
    } catch { return $false }
}

$checks = @(
    @{ Name = "Frontend :3001"; Test = { Test-TcpPort "127.0.0.1" 3001 } },
    @{ Name = "Backend  :5000"; Test = { Test-TcpPort "127.0.0.1" 5000 } },
    @{ Name = "Neo4j    :7687"; Test = { Test-TcpPort "127.0.0.1" 7687 } },
    @{ Name = "Neo4j UI :7474"; Test = { Test-TcpPort "127.0.0.1" 7474 } },
    @{ Name = "Redis    :6379"; Test = { Test-TcpPort "127.0.0.1" 6379 } },
    @{ Name = "MinIO    :9000"; Test = { Test-TcpPort "127.0.0.1" 9000 } },
    @{ Name = "MinerU   :8001"; Test = { Test-HttpOk "http://127.0.0.1:8001/health" } },
    @{ Name = "Chroma   :8000"; Test = { Test-TcpPort "127.0.0.1" 8000 } }
)

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Health Check — $ProjectRoot" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$fail = 0
foreach ($c in $checks) {
    $ok = & $c.Test
    if ($ok) {
        Write-Host "  [OK]   $($c.Name)" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $($c.Name)" -ForegroundColor Red
        $fail++
    }
}

Write-Host ""
if ($fail -eq 0) {
    Write-Host "All checks passed." -ForegroundColor Green
    exit 0
} else {
    Write-Host "$fail check(s) failed. Wait for services to start or review start-all.ps1 windows." -ForegroundColor Yellow
    exit 1
}
