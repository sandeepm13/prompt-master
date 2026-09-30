@echo off
REM ================================================================
REM  Builds the app you share with other people:
REM    dist\MasterPrompt\MasterPrompt.exe       the program (no Python needed)
REM    dist\MasterPrompt-Setup-<version>.exe   the installer - THIS is the file to share
REM  Your own config.toml / API key is never included: every user types
REM  their own key in the welcome window on first start.
REM  The installer step needs Inno Setup 6 (free): https://jrsoftware.org/isdl.php
REM ================================================================
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Run setup.bat first.
    pause
    exit /b 1
)

echo [1/3] Installing the build tool (PyInstaller) ...
.venv\Scripts\python.exe -m pip install --quiet pyinstaller || goto :fail

echo [2/3] Building dist\MasterPrompt\MasterPrompt.exe ...
if not exist assets mkdir assets
.venv\Scripts\python.exe -c "from master_prompt.tray import make_icon_image as m; m(256).save('assets/icon.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])" || goto :fail
for /f %%v in ('.venv\Scripts\python.exe -c "import master_prompt; print(master_prompt.__version__)"') do set VERSION=%%v
.venv\Scripts\pyinstaller.exe --noconfirm --clean --log-level WARN --windowed --name MasterPrompt ^
  --icon assets\icon.ico --add-data "config.example.toml;." --hidden-import pystray._win32 ^
  launcher.py || goto :fail

echo [3/3] Building the installer ...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 7\ISCC.exe"
if not exist "%ISCC%" (
    echo  Inno Setup 6 is not installed, so only the .exe was built.
    echo  Get it from https://jrsoftware.org/isdl.php and run this file again.
    goto :done
)
"%ISCC%" /Q /DAppVersion=%VERSION% installer.iss || goto :fail
echo.
echo  Done! Share this file:  dist\MasterPrompt-Setup-%VERSION%.exe

:done
pause
exit /b 0

:fail
echo.
echo  Build failed - see the messages above.
pause
exit /b 1
