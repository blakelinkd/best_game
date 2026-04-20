@echo off
echo Steam Twitch Viewer Dashboard
echo =============================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    pause
    exit /b 1
)

python main.py %*

pause
