@echo off
title AI DJ - Backend Server
cd /d "%~dp0"

echo.
echo  +======================================+
echo  ^|             AI DJ Server             ^|
echo  +======================================+
echo.

REM -- Check Python --------------------------------------------------
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python not found. Install Python 3.11+ and add to PATH.
    pause
    exit /b 1
)

REM -- Create venv if missing ----------------------------------------
if not exist "backend\.venv" (
    echo [SETUP] Creating virtual environment...
    python -m venv backend\.venv
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
)

REM -- Activate venv -------------------------------------------------
call backend\.venv\Scripts\activate.bat

REM -- Install / upgrade dependencies --------------------------------
echo [SETUP] Installing dependencies...
pip install --quiet --upgrade -r backend\requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)

REM -- Check .env exists ---------------------------------------------
if not exist "backend\.env" (
    echo [ERROR] backend\.env not found!
    echo         Create it with:
    echo           GEMINI_API_KEY=your_key_here
    echo           MUSIC_DIR=local_path_to_music_files
    pause
    exit /b 1
)

REM -- Launch server -------------------------------------------------
echo.
echo [START] Launching AI DJ backend on http://localhost:8000
echo         Press Ctrl+C to stop.
echo.

set PYTHONIOENCODING=utf-8
python backend\main.py

pause