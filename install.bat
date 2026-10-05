@echo off
REM FormuMind 一键安装 (Windows)
REM 双击即可运行；自动绕过 PowerShell 执行策略限制。
REM 参数会原样转发，例如: install.bat -Minimal / -Full / -SkipFrontend
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set EXITCODE=%ERRORLEVEL%
endlocal & exit /b %EXITCODE%
