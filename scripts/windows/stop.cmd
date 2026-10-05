@echo off
REM FormuMind - stop the host-side stack (Docker infra is left running).
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-dev.ps1" stop %*
set EXITCODE=%ERRORLEVEL%
endlocal & exit /b %EXITCODE%
