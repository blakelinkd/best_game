@echo off
set "VENV_PYTHON=python_venv\Scripts\python.exe"

echo Steam Twitch Viewer Dashboard
echo =============================
echo.

if not exist "%VENV_PYTHON%" (
    echo ERROR: Virtual environment not found. Run setup_windows.bat first.
    pause
    exit /b 1
)

"%VENV_PYTHON%" main.py %*

pause
