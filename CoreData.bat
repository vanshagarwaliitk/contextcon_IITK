@echo off
rem CoreData launcher: double-click to run. First run sets everything up automatically.
setlocal
cd /d "%~dp0"
title CoreData

if not exist ".venv\Scripts\python.exe" (
    echo Setting up CoreData for the first time. This takes a minute...
    where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo Python 3.11 or newer is needed. Install it from https://www.python.org/downloads/ and run this again.
        pause
        exit /b 1
    )
)

rem (Re)install dependencies when requirements.txt changes.
fc /b requirements.txt ".venv\.requirements.installed" >nul 2>nul
if errorlevel 1 (
    echo Installing Python packages...
    ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo Package installation failed. Check your internet connection and run this again.
        pause
        exit /b 1
    )
    copy /y requirements.txt ".venv\.requirements.installed" >nul
)

".venv\Scripts\python.exe" -m scripts.wizard
echo.
pause
