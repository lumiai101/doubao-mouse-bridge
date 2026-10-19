@echo off
setlocal
where uv >nul 2>&1
if errorlevel 1 (
  echo uv is required. See README.md for installation instructions.
  pause
  exit /b 1
)
uv venv --python 3.13 "%~dp0.venv"
if errorlevel 1 goto failed
uv pip install --python "%~dp0.venv\Scripts\python.exe" -r "%~dp0requirements.txt"
if errorlevel 1 goto failed
"%~dp0.venv\Scripts\python.exe" -B -I "%~dp0tests\test_bridge.py"
if errorlevel 1 goto failed
echo Setup complete. Run start.cmd to start the bridge.
pause
exit /b 0
:failed
echo Setup failed. Review the error above. No drivers or startup entries were installed.
pause
exit /b 1
