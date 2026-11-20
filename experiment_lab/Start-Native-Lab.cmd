@echo off
setlocal
cd /d "%~dp0"
py -3 scripts\launch_recording_lab.py
if errorlevel 1 pause
