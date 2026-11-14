@echo off
rem  Market Tracker
setlocal
cd /d "%~dp0market_tracker"
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo Install it from https://www.python.org/downloads/
    echo IMPORTANT: check "Add python.exe to PATH" during install.
    pause
    exit /b 1
)
if not exist "assets\background.jpg" (
    echo [ERROR] assets folder not found next to main.py.
    echo Make sure the whole market_tracker folder stays together.
    pause
    exit /b 1
)
python main.py
if errorlevel 1 pause
endlocal
