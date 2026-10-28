@echo off
title Build Firoz Download Manager
python --version >nul 2>&1 || (echo Python is not installed. Get it from https://www.python.org/downloads/ ^(tick "Add Python to PATH"^) & pause & exit /b)
python -m pip install --upgrade pyinstaller
set ICON=
if exist icon.ico set ICON=--icon icon.ico
pyinstaller --noconfirm --onefile --windowed %ICON% --name "Firoz Download Manager" firoz_download_manager.py
echo.
echo Done!  Your program:  dist\Firoz Download Manager.exe
echo Keep the "browser_extension" folder next to the exe if you want browser integration.
pause
