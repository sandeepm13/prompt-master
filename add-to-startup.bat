@echo off
REM Makes Master Prompt start automatically when you log in to Windows.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup') + '\Master Prompt.lnk');" ^
  "$s.TargetPath='%~dp0.venv\Scripts\pythonw.exe'; $s.Arguments='-m master_prompt';" ^
  "$s.WorkingDirectory='%~dp0'; $s.Description='Master Prompt'; $s.Save()"
if errorlevel 1 (
    echo Failed to create the startup shortcut.
) else (
    echo Done. Master Prompt will start automatically when you log in.
)
pause
