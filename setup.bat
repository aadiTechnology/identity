@echo off
rem First-time setup of Aadi Identity on a developer machine (Windows). Safe to run again.
rem Needs: Python 3.12+, Docker Desktop (for PostgreSQL). Result: database ready, products registered, the Aadi-Test company seeded.
setlocal
cd /d "%~dp0"

where python >nul 2>nul || (echo Python is not installed or not on PATH. Install Python 3.12 or newer from python.org and run again. & exit /b 1)
where docker >nul 2>nul || (echo Docker is not installed. Install Docker Desktop and run again. & exit /b 1)
docker info >nul 2>nul || (echo Docker is installed but not running. Start Docker Desktop, wait until it says running, and run again. & exit /b 1)

if not exist .venv (
  echo Creating the Python environment...
  python -m venv .venv || exit /b 1
)
echo Installing packages...
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements-dev.txt || exit /b 1

if not exist .env (
  echo Writing .env for local development...
  (
    echo APP_ENV=demo
    echo ISSUER=http://localhost:8010
    echo DATABASE_URL=postgresql+psycopg://identity:identity@localhost:15440/identity
    echo COOKIE_SECURE=false
    echo EMAIL_BACKEND=file
    echo WEBHOOKS_ENABLED=false
    echo TEST_LOGINS_ENABLED=true
  ) > .env
)

echo Starting PostgreSQL (port 15440)...
docker compose up -d postgres || exit /b 1
echo Waiting for the database...
set /a tries=0
:wait
docker compose exec -T postgres pg_isready -U identity -d identity >nul 2>nul && goto ready
set /a tries+=1
if %tries% GEQ 40 (echo The database did not become ready. Run "docker compose logs postgres". & exit /b 1)
timeout /t 2 /nobreak >nul
goto wait
:ready

echo Creating tables...
.venv\Scripts\python.exe -c "from app.main import migrate; migrate()" || exit /b 1
echo Registering Project Tracker, Academic Video Creation and the Video Engine, and seeding the Aadi-Test company...
.venv\Scripts\python.exe -m app.cli dev-setup || exit /b 1

echo.
echo Identity is ready. Start it with run.bat, then open http://localhost:8010
echo Every Aadi-Test person is listed under the sign-in form (password AadiTest@2026).
echo Tests: .venv\Scripts\python.exe -m pytest -q
endlocal
