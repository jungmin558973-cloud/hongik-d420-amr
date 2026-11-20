@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
if errorlevel 1 exit /b 1
if not exist ".venv\installed.ok" (
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 exit /b 1
  type nul > .venv\installed.ok
)
start "" http://127.0.0.1:18865/experiment-lab
.venv\Scripts\python.exe -m lab.server --mock-camera
pause
