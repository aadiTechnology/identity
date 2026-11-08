#!/bin/sh
# Migrations run at start. Operator commands: docker exec -it <container> python -m app.cli --help
set -e
python -c "from app.main import migrate; migrate()"
# Demo data (fictional) only when explicitly requested and never in production.
if [ "$APP_SEED_DEMO" = "true" ]; then
  if [ "$APP_ENV" = "production" ]; then echo "APP_SEED_DEMO=true is not allowed with APP_ENV=production"; exit 1; fi
  python -m app.cli seed-demo
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*"
