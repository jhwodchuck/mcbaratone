@echo off
echo Building React Dashboard...
cd dashboard
call npm run build
if %ERRORLEVEL% neq 0 (
    echo React build failed!
    cd ..
    exit /b %ERRORLEVEL%
)
cd ..

set PYTHON_CMD=python
if exist .venv\Scripts\python.exe (
    set PYTHON_CMD=.venv\Scripts\python.exe
) else if exist C:\gh\.venvs\mcbaratone\Scripts\python.exe (
    set PYTHON_CMD=C:\gh\.venvs\mcbaratone\Scripts\python.exe
)

echo Starting Python Dashboard Server using %PYTHON_CMD%...
%PYTHON_CMD% scripts\dashboard_server.py %*
