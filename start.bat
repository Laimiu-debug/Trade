@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start-dev.ps1" %*

if errorlevel 1 (
  echo.
  echo Startup failed. See backend-dev.err.log and frontend-dev.err.log for details.
  exit /b 1
)

exit /b 0
