#!/bin/sh
# Migrations run at start. Operator commands: docker exec -it <container> python -m app.cli --help
set -e
python -c "from app.main import migrate; migrate()"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*"
