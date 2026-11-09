@echo off
rem ---------------------------------------------------------------------------
rem py.cmd - unified entry point for FULIBA scripts (the ONLY entry point)
rem
rem   Call it from the project root with the _scripts\ prefix, e.g.
rem       _scripts\py.cmd _scripts\build\rebuild.py --check
rem   There used to be a 3-line forwarding shim at the project root so you could
rem   type a bare py.cmd there; it was deleted on 2026-11-06 because the site root
rem   should hold nothing but the site shell. Update all docs to the prefixed form.
rem
rem usage:
rem   _scripts\py.cmd _scripts\build\rebuild.py --check
rem   _scripts\py.cmd _scripts\build\update.py
rem
rem behaviour:
rem   Try the venv interpreter first. If its stdlib is missing (WorkBuddy
rem   update incident, see plan section 1.38), fall back to a complete
rem   interpreter of the same version and reuse the venv site-packages.
rem   Keep this file ASCII-only: cmd.exe parses it as the OEM codepage and
rem   multi-byte Chinese comments can break the rem lines.
rem ---------------------------------------------------------------------------
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "VENV_PY=C:\Users\wh\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
set "ALT_PY=C:\Users\wh\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe"
set "SP=C:\Users\wh\.workbuddy\binaries\python\envs\default\Lib\site-packages"

"%VENV_PY%" -c "import encodings" >nul 2>&1
if errorlevel 1 goto :fallback

"%VENV_PY%" -u %*
goto :eof

:fallback
if not exist "%ALT_PY%" (
  echo [py.cmd] no usable Python interpreter, please repair the WorkBuddy runtime 1>&2
  exit /b 9
)
set "PYTHONPATH=%SP%"
"%ALT_PY%" -u %*
goto :eof
