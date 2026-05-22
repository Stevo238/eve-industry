@echo off
cd /d "%~dp0"
echo Starting EVE Industry Manager on http://localhost:9555
start http://localhost:9555
python run.py
pause
