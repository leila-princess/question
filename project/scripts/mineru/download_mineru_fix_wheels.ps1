# Download MinerU WSL fix wheels (Linux cp310, small pack for USB copy)
# Usage: .\scripts\mineru\download_mineru_fix_wheels.ps1

param(
    [string]$OutDir = "",
    [string]$Python = "",
    [string]$Platform = "manylinux2014_x86_64",
    [string]$PyVer = "3.10",
    [string]$Abi = "cp310"
)

$ErrorActionPreference = "Stop"

if (-not $OutDir) {
    $OutDir = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path "0616\packages\pip_wheels_mineru_fix_cp310"
}

function Resolve-PythonExe {
    param([string]$Explicit)
    if ($Explicit -and (Test-Path $Explicit)) { return $Explicit }
    $candidates = @()
    if ($env:CONDA_PREFIX) {
        $candidates += (Join-Path $env:CONDA_PREFIX "python.exe")
    }
    $candidates += @("python", "python3")
    foreach ($c in $candidates) {
        if (-not $c) { continue }
        try {
            $v = & $c -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v) { return $v.Trim() }
        } catch { }
    }
    throw "Python not found. Install Python 3 or pass -Python path."
}

$Py = Resolve-PythonExe -Explicit $Python
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$pipBase = @(
    "-m", "pip", "download", "--no-cache-dir", "-d", $OutDir,
    "--platform", $Platform,
    "--python-version", $PyVer,
    "--abi", $Abi,
    "--only-binary=:all:"
)

$corePkgs = @(
    'numpy==1.26.4',
    'onnxruntime==1.16.3',
    'opencv-python-headless==4.11.0.86',
    'opencv-python==4.11.0.86',
    'magika==0.6.3',
    'pylatexenc==2.10'
)

$officePkgs = @(
    'pypptx-with-oxml==1.0.3',
    'python-pptx',
    'python-docx',
    'openpyxl',
    'mammoth',
    'XlsxWriter',
    'lxml>=4.9,<7',
    'typing_extensions',
    'pypdf',
    'pypdfium2',
    'reportlab',
    'pdftext',
    'json-repair',
    'fastapi',
    'uvicorn',
    'python-multipart'
)

$extraPkgs = @(
    "coloredlogs",
    "humanfriendly",
    "flatbuffers",
    "packaging",
    "protobuf",
    "sympy",
    "mpmath"
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " MinerU fix wheels (Linux cp310)" -ForegroundColor Cyan
Write-Host " Python : $Py" -ForegroundColor Cyan
Write-Host " OutDir : $OutDir" -ForegroundColor Cyan
Write-Host " Platform: $Platform $Abi py$PyVer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

function Download-Req {
    param(
        [string]$Req,
        [switch]$NoDeps
    )
    Write-Host "==> $Req" -ForegroundColor Yellow
    $dlArgs = $pipBase + @($Req)
    if ($NoDeps) { $dlArgs += "--no-deps" }
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Py @dlArgs --index-url https://pypi.org/simple 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  retry pypi no-deps..." -ForegroundColor DarkYellow
            & $Py -m pip download --no-cache-dir -d $OutDir --no-deps --index-url https://pypi.org/simple $Req 2>&1 | ForEach-Object { Write-Host $_ }
        }
    } finally {
        $ErrorActionPreference = $oldEap
    }
}

foreach ($req in $corePkgs) {
    $noDeps = $req -match '^(numpy|onnxruntime|opencv-python|lxml)'
    if ($noDeps) { Download-Req $req -NoDeps } else { Download-Req $req }
}
foreach ($req in $officePkgs) {
    $noDeps = $req -match '^lxml'
    if ($noDeps) { Download-Req $req -NoDeps } else { Download-Req $req }
}
foreach ($req in $extraPkgs) {
    try { Download-Req $req } catch { Write-Host "  skip optional $req" -ForegroundColor DarkGray }
}

$removed = 0
Get-ChildItem -Path $OutDir -Filter "*.whl" | ForEach-Object {
    $n = $_.Name
    if ($n -match "win_amd64|win32|macosx") {
        Remove-Item $_.FullName -Force
        $removed++
        Write-Host "removed non-linux: $n" -ForegroundColor DarkGray
        return
    }
    if ($n -match "^numpy-2\.") {
        Remove-Item $_.FullName -Force
        $removed++
        Write-Host "removed numpy 2.x: $n" -ForegroundColor DarkGray
    }
}

$whls = @(Get-ChildItem -Path $OutDir -Filter "*.whl" | Sort-Object Name)
$manifest = @{
    generated_at = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    platform     = $Platform
    python       = $PyVer
    abi          = $Abi
    wheel_count  = $whls.Count
    wheels       = @($whls | ForEach-Object { $_.Name })
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $OutDir "manifest.json") -Encoding UTF8

Write-Host ""
Write-Host "Done: $($whls.Count) wheels -> $OutDir" -ForegroundColor Green
if ($removed -gt 0) { Write-Host "Removed $removed non-linux wheels" -ForegroundColor DarkYellow }

$checks = @(
    "numpy-1.*manylinux*.whl",
    "onnxruntime*manylinux*.whl",
    "opencv_python_headless*manylinux*.whl",
    "pypptx_with_oxml-1.0.3-py3-none-any.whl",
    "magika-0.6.3-py3-none-any.whl"
)
foreach ($pat in $checks) {
    $hit = Get-ChildItem -Path $OutDir -Filter $pat -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($hit) {
        Write-Host "  OK $($hit.Name)" -ForegroundColor Green
    } else {
        Write-Host "  WARN missing $pat" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Copy folder pip_wheels_mineru_fix_cp310 to target machine only." -ForegroundColor Cyan
Write-Host "WSL install: bash scripts/mineru/wsl/fix_mineru_numpy_stack.sh" -ForegroundColor Cyan
