@echo off
REM Starts Master Prompt in the background (tray icon near the clock)
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo Python virtual environment not found in .venv.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m master_prompt

