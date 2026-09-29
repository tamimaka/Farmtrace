@echo off
cd /d "%~dp0"
echo Starting Mbeu Trace Farm Traceability server...
echo Open http://localhost:5000 in your browser.
"%~dp0.venv\Scripts\python.exe" farm_traceability\app.py
pause
