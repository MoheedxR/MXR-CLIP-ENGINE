@echo off
REM Updates MXR CLIP ENGINE to the latest version. Keeps your .env and data folder.
cd /d "%~dp0"
py -3.12 update.py 2>nul || python update.py
pause
