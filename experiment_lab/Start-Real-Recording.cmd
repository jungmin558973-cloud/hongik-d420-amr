@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Start-Mock.cmd once to install dependencies.
  pause
  exit /b 1
)
start "" http://127.0.0.1:18865/experiment-lab
.venv\Scripts\python.exe -m lab.server --real --auto-camera
pause
