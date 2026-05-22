@echo off
call venv\Scripts\activate.bat
uvicorn web.app:app --host 0.0.0.0 --port 8090 --reload
pause