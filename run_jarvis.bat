@echo off
cd /d C:\Users\OLUWASEYI\jarvis_project
echo Activating Virtual Environment...
call venv\Scripts\activate
echo Starting JARVIS Autonomous System...
python3 jarvis.py
if errorlevel 1 (
    echo.
    echo [ERROR] JARVIS crashed or failed to start.
    pause
)
