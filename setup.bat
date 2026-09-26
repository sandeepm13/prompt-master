@echo off
REM ================================================================
REM  Master Prompt - one-time setup. Double-click this file.
REM ================================================================
cd /d "%~dp0"

where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
%PY% -c "import sys; assert sys.version_info >= (3, 11)" 2>nul
if errorlevel 1 (
    echo.
    echo  Python 3.11 or newer is needed.
    echo  Install it from https://www.python.org/downloads/  ^(tick "Add python.exe to PATH"^)
    echo.
    pause
    exit /b 1
)
%PY% -c "import tkinter" 2>nul
if errorlevel 1 (
    echo  Your Python has no tkinter. Re-install Python from python.org with "tcl/tk" ticked.
    pause
    exit /b 1
)

echo [1/3] Creating a private Python environment in .venv ...
if not exist .venv %PY% -m venv .venv || goto :fail

echo [2/3] Installing packages ...
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt || goto :fail

echo [3/3] Creating config.toml ...
if not exist config.toml copy config.example.toml config.toml >nul

echo.
echo  Setup done!
echo  1. Put your Groq / Gemini API key in config.toml (opening it now).
echo  2. Double-click test-key.bat to check the key works.
echo  3. Double-click run.bat to start. Then press Win+Shift+P in any text box.
echo.
start notepad config.toml
pause
exit /b 0

:fail
echo.
echo  Something failed - see the messages above.
pause
exit /b 1
