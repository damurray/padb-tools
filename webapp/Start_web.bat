@echo off
cd /d "%~dp0"
REM Free port 5000 first: a leftover webapp instance holding it makes a new one
REM silently fail to bind and serve stale, cached code (the "multiple webapp
REM generations" gotcha). Kill only the process LISTENING on :5000.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr "LISTENING" ^| findstr /C:":5000 "') do (
    echo Freeing port 5000 ^(was held by PID %%P^)...
    taskkill /F /PID %%P >nul 2>&1
)
py padb_web.py
