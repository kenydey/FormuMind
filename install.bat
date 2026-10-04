@echo off
REM FormuMind 一键安装 (Windows)
REM 双击即可运行；自动绕过 PowerShell 执行策略限制。
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
endlocal
