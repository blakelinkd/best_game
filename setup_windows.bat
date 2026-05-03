@echo off
setlocal enabledelayedexpansion

echo =============================================
echo  Best Game - Windows Setup
echo =============================================
echo.

REM ---- configurable ----
set "PYTHON_VERSION=3.13.7"
set "PYTHON_DIR=python_portable"
set "VENV_DIR=python_venv"
set "PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%-embed-amd64.zip"
set "GET_PIP_URL=https://bootstrap.pypa.io/get-pip.py"

REM ---- helper to call powershell ----
set "PS=powershell -NoProfile -NonInteractive -Command"

REM ---- check Windows version (10+) ----
for /f "tokens=2 delims=[]" %%v in ('ver') do set "WINVER=%%v"
for /f "tokens=2 delims=. " %%v in ("!WINVER!") do set "WINMAJOR=%%v"
if !WINMAJOR! LSS 10 (
    echo [ERROR] Windows 10 or later is required.
    pause
    exit /b 1
)

REM ---- already set up? ----
if exist "%VENV_DIR%\Scripts\python.exe" (
    echo [INFO] Virtual environment already exists. Running dependency check...
    call "%VENV_DIR%\Scripts\python.exe" setup_deps.py
    if !errorlevel! equ 0 goto :done
    echo [WARN] Dependency check had issues - continuing full setup.
)

REM ---- download + extract embeddable Python ----
if not exist "%PYTHON_DIR%\python.exe" (
    echo [1/4] Downloading Python %PYTHON_VERSION% portable...
    if exist "%PYTHON_DIR%" rmdir /s /q "%PYTHON_DIR%"
    mkdir "%PYTHON_DIR%" 2>nul
    %PS% "Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_DIR%\python.zip' -ErrorAction Stop"
    if !errorlevel! neq 0 goto :error

    echo [2/4] Extracting Python...
    %PS% "Expand-Archive -LiteralPath '%PYTHON_DIR%\python.zip' -DestinationPath '%PYTHON_DIR%' -Force"
    if !errorlevel! neq 0 goto :error
    del "%PYTHON_DIR%\python.zip"

    REM ---- enable site-packages so pip works ----
    for %%f in ("%PYTHON_DIR%\*._pth") do (
        %PS% "(Get-Content '%%f') -replace '^#import site', 'import site' | Set-Content '%%f'"
    )

    REM ---- install pip and virtualenv into the portable Python ----
    echo [3/5] Installing pip...
    %PS% "Invoke-WebRequest -Uri '%GET_PIP_URL%' -OutFile '%PYTHON_DIR%\get-pip.py' -ErrorAction Stop"
    if !errorlevel! neq 0 goto :error
    "%PYTHON_DIR%\python.exe" "%PYTHON_DIR%\get-pip.py" --no-warn-script-location
    if !errorlevel! neq 0 goto :error
    del "%PYTHON_DIR%\get-pip.py"

    echo [4/5] Installing virtualenv...
    "%PYTHON_DIR%\python.exe" -m pip install virtualenv --no-warn-script-location
    if !errorlevel! neq 0 goto :error
) else (
    echo [SKIP] Portable Python already present.
)

REM ---- create venv (using virtualenv, embeddable Python lacks stdlib venv) ----
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [5/5] Creating virtual environment ^(%VENV_DIR%^)...
    "%PYTHON_DIR%\python.exe" -m virtualenv "%VENV_DIR%"
    if !errorlevel! neq 0 goto :error
) else (
    echo [SKIP] Virtual environment already exists.
)

REM ---- install project dependencies ----
echo.
echo Installing project dependencies...
call "%VENV_DIR%\Scripts\python.exe" setup_deps.py
if !errorlevel! neq 0 goto :error

:done
echo.
echo =============================================
echo  Setup complete!
echo.
echo  Activate the environment:
echo    %VENV_DIR%\Scripts\activate.bat
echo.
echo  Run the app:
echo    python main.py
echo =============================================
endlocal
exit /b 0

:error
echo.
echo [ERROR] Setup failed. See messages above.
pause
endlocal
exit /b 1
