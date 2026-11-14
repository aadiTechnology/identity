<#
 Local staging gate (deviation D-6): starts Identity (8010), Project Tracker (8000), Academics (API 8100, web 5174) and the Video Engine (8300)
 on local PostgreSQL containers with Aadi-Test data, then smoke-checks every service. Exit code 0 = gate passed. Use before every deploy.
 Needs Docker containers: identity-postgres-1 (15440), tracker-postgres (15434), aadi-engine-test-pg (15441).
 Usage: powershell -File ops\staging_gate.ps1 [-LogDir <folder>]
#>
param([string]$LogDir = (Join-Path $env:TEMP "aadi-staging-gate"))
$ErrorActionPreference = "Continue"
$root = "C:\Users\lenovo\AadiTech\Product\Aadi-Product"
$sp = $LogDir
New-Item -ItemType Directory -Force $sp | Out-Null
foreach ($c in "identity-postgres-1", "tracker-postgres", "aadi-engine-test-pg") { docker start $c 2>$null | Out-Null }
Start-Sleep 6
foreach ($port in 8010, 8000, 8100, 5174, 8300) {
  Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*run_engine_demo.py*worker*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 2

# ---- databases that need creating / resetting ----
foreach ($db in "engine_demo", "academic_aadi_test") {
  docker exec aadi-engine-test-pg psql -U postgres -c "DROP DATABASE IF EXISTS $db" 2>$null | Out-Null
  docker exec aadi-engine-test-pg psql -U postgres -c "CREATE DATABASE $db" 2>$null | Out-Null
}
docker exec tracker-postgres psql -U tracker -d postgres -c "DROP DATABASE IF EXISTS tracker_story" 2>$null | Out-Null
docker exec tracker-postgres psql -U tracker -d postgres -c "CREATE DATABASE tracker_story" 2>$null | Out-Null

# ---- Identity (8010): its local .env, test list on, a platform administrator, the Video Engine switched OFF for Aadi-Test ----
$idr = "$root\identity"
$py = "$idr\.venv\Scripts\python.exe"
docker exec identity-postgres-1 psql -U identity -d postgres -c "DROP DATABASE IF EXISTS identity WITH (FORCE)" 2>$null | Out-Null
docker exec identity-postgres-1 psql -U identity -d postgres -c "CREATE DATABASE identity" 2>$null | Out-Null
Push-Location $idr
& $py -c "from app.main import migrate; migrate()" 2>&1 | Out-Null
$env:TEST_LOGINS_ENABLED = "true"
& $py -m app.cli dev-setup 2>&1 | Out-Null
$env:IDENTITY_ADMIN_PASSWORD = "Platform-Demo-1"
& $py -m app.cli create-admin --email platform@aadi-demo.example.com --name "Platform Admin" 2>&1 | Out-Null
$env:PYTHONPATH = $idr
& $py -c "from app.db import SessionLocal; from app import models as m; db=SessionLocal(); db.query(m.Subscription).filter_by(tenant_id='5ca1ab1e-aad1-4e57-8000-000000000001', product_key='video-engine').delete(); db.commit()"
Remove-Item Env:PYTHONPATH
Pop-Location
Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "8010" -WorkingDirectory $idr -WindowStyle Hidden -RedirectStandardOutput "$sp\identity.log" -RedirectStandardError "$sp\identity.err"

# ---- Project Tracker (8000): Aadi-Test from the workbook, Aadi sign-in ----
$trk = "$root\ProjectTracking\backend"
$env:APP_ENV = "demo"; $env:DATABASE_URL = "postgresql+psycopg://tracker:tracker_dev@localhost:15434/tracker_story"
Push-Location $trk
& "$trk\.venv\Scripts\python.exe" -m app.seed_aadi_test 2>&1 | Select-Object -Last 2
Pop-Location
$env:AUTH_MODE = "both"; $env:IDENTITY_ISSUER = "http://localhost:8010"; $env:IDENTITY_CLIENT_ID = "tracker-web"; $env:APP_BASE_URL = "http://localhost:8000"
Start-Process -FilePath "$trk\.venv\Scripts\python.exe" -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "8000" -WorkingDirectory $trk -WindowStyle Hidden -RedirectStandardOutput "$sp\tracker.log" -RedirectStandardError "$sp\tracker.err"
Remove-Item Env:AUTH_MODE, Env:IDENTITY_ISSUER, Env:IDENTITY_CLIENT_ID, Env:APP_BASE_URL, Env:APP_ENV, Env:DATABASE_URL -ErrorAction SilentlyContinue

# ---- Video Engine (8300): tokens only, Windows voice standing in for Azure ----
$eng = "$root\Media\ScriptToVideo"
$data = Join-Path $sp "engine_data"
Remove-Item $data -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $data | Out-Null
$env:ENGINE_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:15441/engine_demo"; $env:ENGINE_DATA_DIR = $data
$env:ENGINE_AUTH_MODE = "token"; $env:ENGINE_IDENTITY_ISSUER = "http://localhost:8010"; $env:ENGINE_ENFORCE_CONTEXT = "true"
$env:ENGINE_MODE = "service"; $env:AZURE_SPEECH_KEY = "demo-only"; $env:AZURE_SPEECH_REGION = "demo-only"
Push-Location $eng
& "$eng\.venv\Scripts\python.exe" -m engine.migrate 2>&1 | Out-Null
Pop-Location
Start-Process -FilePath "$eng\.venv\Scripts\python.exe" -ArgumentList "docs\demos\run_engine_demo.py", "8300" -WorkingDirectory $eng -WindowStyle Hidden -RedirectStandardOutput "$sp\engine_api.log" -RedirectStandardError "$sp\engine_api.err"
Start-Process -FilePath "$eng\.venv\Scripts\python.exe" -ArgumentList "docs\demos\run_engine_demo.py", "worker" -WorkingDirectory $eng -WindowStyle Hidden -RedirectStandardOutput "$sp\engine_worker.log" -RedirectStandardError "$sp\engine_worker.err"
Remove-Item Env:ENGINE_DATABASE_URL, Env:ENGINE_DATA_DIR, Env:ENGINE_AUTH_MODE, Env:ENGINE_IDENTITY_ISSUER, Env:ENGINE_ENFORCE_CONTEXT, Env:ENGINE_MODE -ErrorAction SilentlyContinue

# ---- Academics (API 8100, web 5174): Aadi sign-in, tokens for the engine ----
$acd = "$root\Media\Academics\backend"
$store = Join-Path $sp "academic_storage"
Remove-Item $store -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $store | Out-Null
$env:DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:15441/academic_aadi_test"; $env:STORAGE_LOCAL_DIR = $store; $env:APP_ENV = "development"
Push-Location $acd
& "$acd\.venv\Scripts\python.exe" -m app.migrate 2>&1 | Out-Null
$env:AUTH_MODE = "dev"
& "$acd\.venv\Scripts\python.exe" -m app.seed_aadi_test 2>&1 | Select-Object -Last 2
Pop-Location
$env:AUTH_MODE = "oidc"; $env:SESSION_SECRET = "story-session-secret-at-least-32-characters-long"; $env:APP_BASE_URL = "http://localhost:5174"
$env:IDENTITY_ISSUER_URL = "http://localhost:8010"; $env:IDENTITY_CLIENT_ID = "academics-web"; $env:IDENTITY_CLIENT_SECRET = "dev-academics-secret"
$env:VIDEO_ENGINE_URL = "http://127.0.0.1:8300"; $env:VIDEO_ENGINE_AUTH = "token"; $env:VIDEO_ENGINE_CLIENT_ID = "academic-video"; $env:VIDEO_ENGINE_CLIENT_SECRET = "dev-video-secret"
Start-Process -FilePath "$acd\.venv\Scripts\python.exe" -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "8100" -WorkingDirectory $acd -WindowStyle Hidden -RedirectStandardOutput "$sp\academic_api.log" -RedirectStandardError "$sp\academic_api.err"
Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run dev" -WorkingDirectory "$root\Media\Academics\frontend" -WindowStyle Hidden -RedirectStandardOutput "$sp\academic_web.log" -RedirectStandardError "$sp\academic_web.err"

# ---- smoke checks ----
function Wait-Ok($name, $url) {
  for ($i = 0; $i -lt 40; $i++) {
    try { $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5; if ($r.StatusCode -eq 200) { Write-Host "PASS  $name  $url"; return $true } } catch {}
    Start-Sleep 3
  }
  Write-Host "FAIL  $name  $url"; return $false
}
$ok = $true
$ok = (Wait-Ok "Identity ready" "http://localhost:8010/health/ready") -and $ok
$ok = (Wait-Ok "Identity OIDC discovery" "http://localhost:8010/.well-known/openid-configuration") -and $ok
$ok = (Wait-Ok "Identity keys" "http://localhost:8010/.well-known/jwks.json") -and $ok
$ok = (Wait-Ok "Tracker" "http://localhost:8000/api/health") -and $ok
$ok = (Wait-Ok "Academics API" "http://localhost:8100/health") -and $ok
$ok = (Wait-Ok "Academics web" "http://localhost:5174/") -and $ok
$ok = (Wait-Ok "Video Engine" "http://localhost:8300/health") -and $ok
if ($ok) { Write-Host "STAGING GATE PASSED"; exit 0 } else { Write-Host "STAGING GATE FAILED (logs in $sp)"; exit 1 }
