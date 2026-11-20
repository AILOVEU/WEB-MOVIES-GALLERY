@echo off
rem FULIBA incremental update launcher.
rem
rem Why this file exists:
rem   1) Windows console defaults to code page 936 (GBK). Python prints UTF-8,
rem      so Chinese turns into mojibake. chcp 65001 fixes it.
rem   2) Do NOT pipe this through Select-Object / more / findstr in PowerShell:
rem      those buffer the whole stream and show nothing until the process exits,
rem      which makes a multi-minute crawl look like a freeze.
rem
rem Keep this file PURE ASCII: cmd.exe parses .bat byte-by-byte and a UTF-8
rem multi-byte char inside an echo line corrupts the following command.
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "PY=%PYTHON%"
if defined PY goto :have_py
where python >nul 2>&1
if not errorlevel 1 (
  set "PY=python"
  goto :have_py
)
where py >nul 2>&1
if not errorlevel 1 (
  set "PY=py -3"
  goto :have_py
)
echo.
echo [update] ERROR: no usable Python interpreter found.
echo   Install Python 3, or set the PYTHON environment variable.
echo.
pause
exit /b 9

:have_py
cd /d "%~dp0"
%PY% -u _scripts\build\update.py %*
set "RC=%ERRORLEVEL%"
echo.
echo [update] exit code: %RC%
pause
exit /b %RC%