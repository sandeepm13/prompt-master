@echo off
REM Stops Master Prompt from starting automatically.
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Startup') + '\Master Prompt.lnk')"
echo Removed from startup.
pause
