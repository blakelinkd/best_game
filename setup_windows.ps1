#Requires -Version 5.1

<#
.SYNOPSIS
    Best Game Windows setup - downloads portable Python, creates venv, installs deps.
.DESCRIPTION
    Run this script to set up a self-contained Python environment for the project.
    Nothing is installed system-wide.
.NOTES
    If you get a permissions error, run:
        powershell -ExecutionPolicy Bypass -File setup_windows.ps1
#>

$ErrorActionPreference = "Stop"

# ---- configurable ----
$PythonVersion   = "3.13.7"
$PythonDir       = "python_portable"
$VenvDir         = "python_venv"
$PythonUrl       = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$GetPipUrl       = "https://bootstrap.pypa.io/get-pip.py"

# ---- helpers ----
function Write-Step($msg) {
    Write-Host $msg -ForegroundColor Cyan
}
function Write-OK($msg) {
    Write-Host "  [OK] $msg" -ForegroundColor Green
}
function Write-Skip($msg) {
    Write-Host "  [SKIP] $msg" -ForegroundColor Yellow
}

Write-Host "=============================================" -ForegroundColor Magenta
Write-Host " Best Game - Windows Setup (PowerShell)" -ForegroundColor Magenta
Write-Host "=============================================" -ForegroundColor Magenta
Write-Host ""

# ---- check OS ----
if ($PSVersionTable.PSVersion.Major -lt 5) {
    Write-Host "[ERROR] PowerShell 5.1 or later required." -ForegroundColor Red
    exit 1
}
$osVersion = [Environment]::OSVersion.Version
if ($osVersion.Major -lt 10) {
    Write-Host "[ERROR] Windows 10 or later required." -ForegroundColor Red
    exit 1
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

# ---- already set up? ----
$venvPython = Join-Path $VenvDir "Scripts\python.exe"
if (Test-Path $venvPython) {
    Write-Step "Virtual environment already exists. Running dependency check..."
    try {
        & $venvPython (Join-Path $projectRoot "setup_deps.py")
        if ($LASTEXITCODE -eq 0) {
            Show-Done
            exit 0
        }
    } catch {}
    Write-Host "  [WARN] Dependency check had issues - continuing full setup." -ForegroundColor Yellow
}

# ---- download embeddable Python ----
$pythonExe = Join-Path $PythonDir "python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Step "[1/4] Downloading Python $PythonVersion portable..."
    if (Test-Path $PythonDir) { Remove-Item -Recurse -Force $PythonDir }
    New-Item -ItemType Directory -Path $PythonDir -Force | Out-Null
    $zipPath = Join-Path $PythonDir "python.zip"
    Invoke-WebRequest -Uri $PythonUrl -OutFile $zipPath -ErrorAction Stop
    Write-OK "Downloaded"

    Write-Step "[2/4] Extracting Python..."
    Expand-Archive -LiteralPath $zipPath -DestinationPath $PythonDir -Force
    Remove-Item $zipPath -Force
    Write-OK "Extracted"

    # ---- enable site-packages so pip works ----
    $pthFile = Get-ChildItem -Path $PythonDir -Filter "*._pth" | Select-Object -First 1
    if ($pthFile) {
        $content = Get-Content $pthFile.FullName -Raw
        $content = $content -replace '(?m)^#import site', 'import site'
        Set-Content -Path $pthFile.FullName -Value $content -NoNewline
    }

    # ---- install pip and virtualenv ----
    Write-Step "[3/5] Installing pip..."
    $getPipPath = Join-Path $PythonDir "get-pip.py"
    Invoke-WebRequest -Uri $GetPipUrl -OutFile $getPipPath -ErrorAction Stop
    & $pythonExe $getPipPath --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw "get-pip.py failed" }
    Remove-Item $getPipPath -Force
    Write-OK "pip installed"

    Write-Step "[4/5] Installing virtualenv..."
    & $pythonExe -m pip install virtualenv --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw "virtualenv install failed" }
    Write-OK "virtualenv installed"
} else {
    Write-Skip "Portable Python already present"
}

# ---- create venv (using virtualenv, embeddable Python lacks stdlib venv) ----
if (-not (Test-Path $venvPython)) {
    Write-Step "[5/5] Creating virtual environment ($VenvDir)..."
    & $pythonExe -m virtualenv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "virtualenv creation failed" }
    Write-OK "Virtual environment created"
} else {
    Write-Skip "Virtual environment already exists"
}

# ---- install project dependencies ----
Write-Step "Installing project dependencies..."
& $venvPython (Join-Path $projectRoot "setup_deps.py")
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }

Show-Done
exit 0

# ---- finish ----
function Show-Done {
    Write-Host ""
    Write-Host "=============================================" -ForegroundColor Magenta
    Write-Host " Setup complete!" -ForegroundColor Green
    Write-Host ""
    Write-Host " Activate the environment:" -ForegroundColor White
    Write-Host "   .\$VenvDir\Scripts\Activate.ps1" -ForegroundColor Cyan
    Write-Host ""
    Write-Host " Run the app:" -ForegroundColor White
    Write-Host "   python main.py" -ForegroundColor Cyan
    Write-Host "=============================================" -ForegroundColor Magenta
}
