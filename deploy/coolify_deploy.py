"""Create or update the Aadi Identity resource in Coolify through its API, then deploy it. Idempotent.
Reads ../.env.coolify (git-ignored, never printed). On first run, missing values (database password, Coolify connection,
mail settings) are copied from the Project Tracker env file given with --from-env. Usage:
  python deploy/coolify_deploy.py [--from-env path/to/.env.coolify] [--no-deploy]"""
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DEMO = "--demo" in sys.argv  # a separate resource, database and address with fictional data
ENV_FILE = HERE / (".env.coolify.demo" if DEMO else ".env.coolify")


def read_env(p: Path) -> dict:
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf8").splitlines():
            line = line.rstrip("\r")
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


env = read_env(ENV_FILE)
if "--from-env" in sys.argv:
    src = read_env(Path(sys.argv[sys.argv.index("--from-env") + 1]))
    for k in ("COOLIFY_URL", "COOLIFY_API_TOKEN", "EMAIL_BACKEND", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_USE_TLS"):
        if k in src and not env.get(k):
            env[k] = src[k]
env.setdefault("POSTGRES_PASSWORD", secrets.token_urlsafe(24))
env.setdefault("ISSUER", "https://id-demo.aadiprojecttracker.aaditechnology.com" if DEMO else "https://id.aadiprojecttracker.aaditechnology.com")
if DEMO:
    env.update({"APP_ENV": "demo", "APP_SEED_DEMO": "true", "EMAIL_BACKEND": "file"})  # demo never sends real e-mail
    for k in [k for k in env if k.startswith("SMTP_")]:
        del env[k]
ENV_FILE.write_text("\n".join(f"{k}={v}" for k, v in env.items()) + "\n", encoding="utf8")

BASE = env["COOLIFY_URL"].rstrip("/") + "/api/v1"
NAME = "aadi-identity-demo" if DEMO else "aadi-identity"
REPO, BRANCH, COMPOSE = "aadiTechnology/identity", "main", "/docker-compose.coolify.yml"
DOMAIN = env["ISSUER"]


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + env["COOLIFY_API_TOKEN"], "Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            t = r.read().decode()
            return r.status, (json.loads(t) if t[:1] in "{[" else t)
    except urllib.error.HTTPError as e:
        t = e.read().decode()
        try:
            return e.code, json.loads(t)
        except ValueError:
            return e.code, t[:400]


def runtime_env() -> dict:
    keep = ("POSTGRES_PASSWORD", "ISSUER", "EMAIL_BACKEND", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_USE_TLS")
    out = {k: env[k] for k in keep if env.get(k)}
    out["APP_ENV"] = env.get("APP_ENV", "production")
    out["APP_SEED_DEMO"] = env.get("APP_SEED_DEMO", "false")
    return out


def main(deploy=True):
    st, servers = call("GET", "/servers")
    server = servers[0]["uuid"]
    st, projects = call("GET", "/projects")
    project = projects[0]["uuid"]
    st, apps = call("GET", "/applications")
    existing = next((a for a in apps if a.get("name", "") == NAME), None)
    domains = [{"name": "app", "domain": DOMAIN}]
    if existing:
        uuid = existing["uuid"]
        print("Updating existing resource", NAME)
        st, r = call("PATCH", f"/applications/{uuid}", {"docker_compose_domains": domains, "git_branch": BRANCH, "docker_compose_location": COMPOSE})
        print("  update:", st)
    else:
        st, ghs = call("GET", "/github-apps")
        gh = next(g["uuid"] for g in ghs if g["name"] != "Public GitHub")
        st, r = call("POST", "/applications/private-github-app", {
            "project_uuid": project, "server_uuid": server, "environment_name": "production", "github_app_uuid": gh,
            "git_repository": REPO, "git_branch": BRANCH, "build_pack": "dockercompose", "ports_exposes": "8000", "base_directory": "/",
            "docker_compose_location": COMPOSE, "name": NAME, "docker_compose_domains": domains, "instant_deploy": False})
        print("  create:", st, "" if st < 300 else r)
        if st >= 300:
            raise SystemExit(1)
        uuid = r["uuid"]
    values = runtime_env()
    data = [{"key": k, "value": v, "is_preview": False, "is_buildtime": False, "is_runtime": True} for k, v in values.items()]
    st, r = call("PATCH", f"/applications/{uuid}/envs/bulk", {"data": data})
    print("  environment variables set:", st, sorted(values))
    print("  resource uuid:", uuid)
    if not deploy:
        return
    st, r = call("POST", f"/deploy?uuid={uuid}&force=true")
    print("  deploy requested:", st, r if st >= 300 else "")
    dep = (r.get("deployments") or [{}])[0].get("deployment_uuid") if isinstance(r, dict) else None
    for _ in range(60):
        time.sleep(10)
        st, d = call("GET", f"/deployments/{dep}") if dep else (0, {})
        status = d.get("status") if isinstance(d, dict) else None
        print("  status:", status, flush=True)
        if status in ("finished", "failed", "cancelled", "error"):
            break
    st, a = call("GET", f"/applications/{uuid}")
    print("  application status:", a.get("status") if isinstance(a, dict) else a)


if __name__ == "__main__":
    main(deploy="--no-deploy" not in sys.argv)
