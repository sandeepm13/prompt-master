@echo off
setlocal
cd /d "%~dp0"
title Building dotix Release...

echo ========================================================
echo   dotix Release Build Runner
echo ========================================================
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" build_release.py %*
) else (
    python build_release.py %*
)

set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% equ 0 (
    echo [SUCCESS] Build completed successfully!
) else (
    echo [ERROR] Build failed with exit code %EXITCODE%.
)
echo.
pause
exit /b %EXITCODE%
