@echo off
REM FormuMind - start the host-side stack (API + Celery worker + Vite).
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-dev.ps1" start %*
set EXITCODE=%ERRORLEVEL%
endlocal & exit /b %EXITCODE%
