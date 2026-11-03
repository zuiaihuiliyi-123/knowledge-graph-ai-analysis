@echo off
chcp 65001
setlocal
cd /d "%~dp0.."
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "%~dp0start.py" %*
) else (
  where py
  if not errorlevel 1 (
    py -3 "%~dp0start.py" %*
  ) else (
    python "%~dp0start.py" %*
  )
)

set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [start] failed, exit code %RC%
  pause
)
exit /b %RC%
