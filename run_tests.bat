@echo off
title AI DJ - Backend Tests
cd /d "%~dp0"

echo.
echo  +======================================+
echo  ^|         AI DJ - Backend Tests        ^|
echo  +======================================+
echo.

REM -- Check Python --------------------------------------------------
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python not found. Install Python 3.11+ and add to PATH.
    pause
    exit /b 1
)

REM -- Reuse the app's venv (created by run.bat), or create it --------
if not exist "backend\.venv" (
    echo [SETUP] Creating virtual environment...
    python -m venv backend\.venv
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
)
call backend\.venv\Scripts\activate.bat

REM -- App deps + pytest ----------------------------------------------
echo [SETUP] Installing dependencies...
pip install --quiet --upgrade -r backend\requirements.txt pytest
if %ERRORLEVEL% neq 0 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)

REM -- Run --------------------------------------------------------------
REM The tests never touch backend\.env, backend\playlists.json,
REM backend\radio_stations.json, backend\podcasts.json, backend\audiobooks.json,
REM your music folder or your audiobook folder:
REM they use a dummy API key, temporary data files, a generated mini audiobook
REM library and mocked network calls (directories, feeds, streaming providers).
REM If Node.js is installed, the frontend unit tests (frontend\tests) run too;
REM otherwise that single test is skipped.
echo.
echo [TEST] Running pytest...
echo.
set PYTHONIOENCODING=utf-8
cd backend
python -m pytest tests -q %*
set RESULT=%ERRORLEVEL%
cd ..

echo.
REM NOTE: no parentheses inside the if-blocks below -- an unescaped ")" in an
REM echo would end the block early and cmd reports ". was unexpected at this time."
if %RESULT% neq 0 (
    echo [FAIL] Some tests failed. Exit code: %RESULT%
) else (
    echo [PASS] All tests passed.
)
pause
exit /b %RESULT%
