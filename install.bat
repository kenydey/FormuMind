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
REM (or the error) with it, so wait for a key - but only then. A terminal or CI run would just block.
REM Explorer starts this file as cmd.exe /c, with the path in two quotes and then a space and a quote
REM (the association is quote-path-quote-space-arguments, and the arguments are empty). Every other way of
REM starting it ends in an argument, in the path's own quote, or is the prompt's command line - measured on
REM Windows in CI, see scripts/windows/README.md. The quotes are turned into Q first, so a path with
REM an ampersand or a parenthesis in it cannot break the test.
set "CL=%cmdcmdline:"=Q%"
if "%CL:~-3%"=="Q Q" pause
endlocal & exit /b %RC%
