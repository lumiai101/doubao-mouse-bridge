@echo off
setlocal
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Run setup.cmd first. See README.md for manual installation.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -B -I "%~dp0src\doubao_mouse_bridge.py" %*
if errorlevel 1 pause
