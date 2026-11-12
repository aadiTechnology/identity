@echo off
rem Starts Aadi Identity for development on http://localhost:8010 (run setup.bat once first).
cd /d "%~dp0"
docker compose up -d postgres >nul 2>nul
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8010 --reload
