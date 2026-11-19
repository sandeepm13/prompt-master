@echo off
REM Starts Master Prompt with a visible console to see real-time logs and errors. Close window to stop.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Python virtual environment not found in .venv.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m master_prompt
pause

