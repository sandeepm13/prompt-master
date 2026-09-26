@echo off
REM Starts Master Prompt WITH a console window so you can see errors. Close the window to stop it.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m master_prompt
pause
