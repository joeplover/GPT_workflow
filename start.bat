@echo off
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
    python -m venv venv
    if errorlevel 1 exit /b 1
)
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
if not exist .env (
    echo Create .env from .env.example and configure DATABASE_URL first.
    pause
    exit /b 1
)
venv\Scripts\python.exe -m uvicorn web.app:app --host 0.0.0.0 --port 8090 --reload --env-file .env
pause
