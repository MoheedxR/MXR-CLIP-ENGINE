@echo off
REM MXR CLIP ENGINE launcher for Windows. Double-click to start.
cd /d "%~dp0"
python run.py
if errorlevel 1 (
  echo.
  pause
)
