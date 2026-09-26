@echo off
REM Starts Master Prompt in the background (look for the icon near the clock).
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo Run setup.bat first.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m master_prompt
