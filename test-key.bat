@echo off
REM Checks config.toml and sends one test prompt to the AI.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m master_prompt --check
echo.
".venv\Scripts\python.exe" -m master_prompt --test "i want to make a website for my college fest using react and it should have registration and payments, how to start"
echo.
pause
