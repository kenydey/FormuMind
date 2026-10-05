@echo off
REM FormuMind - show which components are up.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-dev.ps1" status %*
set EXITCODE=%ERRORLEVEL%
endlocal & exit /b %EXITCODE%
