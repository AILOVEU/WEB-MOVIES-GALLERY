@echo off
rem gallery_delete launcher (repo root) - Windows bat version of gallery_delete.sh.
rem Actual script lives in _tools\gallery_delete\; this wrapper just forwards to it.
rem
rem IMPORTANT: keep this file PURE ASCII.
rem cmd.exe parses .bat byte-by-byte; a UTF-8 multi-byte char inside an echo line
rem makes it swallow the following bytes and corrupt the command (measured:
rem "[2] fetch" turned into "ch"). All Chinese text lives in the Python menu.
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"

rem ---- 1) resolve python interpreter ----
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
where python3 >nul 2>&1
if not errorlevel 1 (
  set "PY=python3"
  goto :have_py
)

echo.
echo [gallery_delete] ERROR: no usable Python interpreter found.
echo   Install Python 3, or set the PYTHON environment variable.
echo.
pause
exit /b 9

:have_py
set "SCRIPT=%~dp0_tools\gallery_delete\gallery_delete.py"

rem ---- 2) no arguments = double-clicked: open the interactive menu ----
if "%~1"=="" goto :menu

rem arguments given: run once; pause only on failure so scripted calls are not blocked
%PY% "%SCRIPT%" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [gallery_delete] failed, exit code %RC%
  pause
)
exit /b %RC%

rem ---- 3) interactive menu (all text comes from the Python side) ----
:menu
%PY% "%SCRIPT%" menu
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [gallery_delete] failed, exit code %RC%
)
pause
exit /b %RC%