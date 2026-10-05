@echo off
REM FormuMind one-click installer (Windows).
REM Double-click to run; bypasses the PowerShell execution policy for this script only.
REM Switches are forwarded as given, e.g.: install.bat -Minimal / -Full / -SkipFrontend
REM Keep this file ASCII: cmd.exe decodes it with the OEM code page (GBK on a Chinese Windows),
REM where the bytes of a UTF-8 character can swallow the line break after it.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "RC=%ERRORLEVEL%"
REM A double-click opens a console that closes together with the script, taking the next-steps
REM (or the error) with it. Pause only in that case, so a terminal or CI run is not blocked.
echo %cmdcmdline% | find /i "%~nx0" >nul && pause
endlocal & exit /b %RC%
