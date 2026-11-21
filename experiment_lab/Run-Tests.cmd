@echo off
setlocal
cd /d "%~dp0"
.venv\Scripts\python.exe -m pytest tests -q --junitxml=test-results.xml
pause
