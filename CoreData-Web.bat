@echo off
rem CoreData web UI: double-click to start. Opens http://localhost:8000 in your browser.
rem Close this window (or press Ctrl+C) to stop the server.
setlocal
cd /d "%~dp0"
title CoreData web UI

rem 1. Python environment (only the first time)
if not exist ".venv\Scripts\python.exe" (
    echo Setting up Python for the first time...
    where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
    if not exist ".venv\Scripts\python.exe" (
        echo Python 3.11 or newer is needed: https://www.python.org/downloads/
        pause
        exit /b 1
    )
)
fc /b requirements.txt ".venv\.requirements.installed" >nul 2>nul
if errorlevel 1 (
    echo Installing Python packages...
    ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || (pause & exit /b 1)
    copy /y requirements.txt ".venv\.requirements.installed" >nul
)

rem 2. Web UI build (only if it hasn't been built yet)
if not exist "frontend\dist\index.html" (
    where npm >nul 2>nul
    if errorlevel 1 (
        echo Node.js is not installed, so the basic built-in page will be used.
        echo Install Node.js 18+ from https://nodejs.org for the full UI.
    ) else (
        echo Building the web UI for the first time...
        pushd frontend
        if not exist "node_modules" call npm install --no-audit --no-fund
        call npm run build
        popd
    )
)

rem 3. Start the server and open the browser once it's up
echo.
echo Starting CoreData at http://localhost:8000  (close this window to stop)
start "" /b powershell -NoProfile -Command "for($i=0;$i -lt 60;$i++){try{Invoke-WebRequest http://localhost:8000/health -UseBasicParsing -TimeoutSec 1 | Out-Null; Start-Process http://localhost:8000; break}catch{Start-Sleep -Milliseconds 500}}"
".venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --port 8000
echo.
echo The server stopped. If port 8000 was busy, close the other CoreData window first.
pause
