"""Load check for the sign-in flow:  python ops/loadtest.py http://localhost:8802 --users 30 --seconds 20

Each virtual person signs in once, then repeats the product flow: authorize (code), token, userinfo, refresh.
Use only against a demo or local instance (it needs the demo people and the demo-tracker client; see `seed-demo`).
"""
import argparse
import base64
import hashlib
import secrets
import statistics
import threading
import time
from collections import defaultdict
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

PEOPLE = ["rahul@demo.example.com", "priya@demo.example.com"]  # both have exactly one Project Tracker company, so authorize goes straight to a code
PASSWORD = "Demo-Password-1"

lat: dict[str, list[float]] = defaultdict(list)
errors: dict[str, int] = defaultdict(int)
lock = threading.Lock()


def timed(name, fn):
    t = time.perf_counter()
    try:
        r = fn()
        ok = r.status_code < 400 or (name == "authorize" and r.status_code == 303)
    except Exception:
        r, ok = None, False
    with lock:
        lat[name].append((time.perf_counter() - t) * 1000)
        if not ok:
            errors[name] += 1
    return r


def worker(base, idx, stop_at):
    c = httpx.Client(base_url=base, follow_redirects=False, timeout=30)
    email = PEOPLE[idx % len(PEOPLE)]
    c.get("/login")
    r = timed("login", lambda: c.post("/login", data={"email": email, "password": PASSWORD, "csrf": c.cookies.get("id_csrf"), "next": "/"}))
    if r is None or r.status_code != 303:
        return
    redirect = f"{base}/demo/tracker/callback"
    while time.time() < stop_at:
        verifier = secrets.token_urlsafe(40)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        q = {"response_type": "code", "client_id": "demo-tracker", "redirect_uri": redirect, "code_challenge": challenge,
             "code_challenge_method": "S256", "state": "s", "tenant": ""}
        q.pop("tenant")
        a = timed("authorize", lambda: c.get("/authorize?" + urlencode(q)))
        if a is None or a.status_code != 303 or "code=" not in a.headers.get("location", ""):
            # a company chooser (several companies): pick nothing and try again
            continue
        code = parse_qs(urlparse(a.headers["location"]).query)["code"][0]
        t = timed("token", lambda: c.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                                                          "code_verifier": verifier, "client_id": "demo-tracker"}))
        if t is None or t.status_code != 200:
            continue
        tok = t.json()
        timed("userinfo", lambda: c.get("/userinfo", headers={"Authorization": "Bearer " + tok["access_token"]}))
        timed("refresh", lambda: c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": "demo-tracker"}))


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--users", type=int, default=30)
    ap.add_argument("--seconds", type=int, default=20)
    a = ap.parse_args()
    start = time.time()
    stop_at = start + a.seconds
    threads = [threading.Thread(target=worker, args=(a.base.rstrip("/"), i, stop_at)) for i in range(a.users)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    took = time.time() - start
    total = sum(len(v) for v in lat.values())
    print(f"{a.users} simultaneous people, {took:.0f}s, {total} requests, {total / took:.1f} requests/s, {sum(errors.values())} errors")
    print(f"{'step':10} {'count':>6} {'errors':>6} {'p50 ms':>8} {'p95 ms':>8} {'p99 ms':>8}")
    for name in ("login", "authorize", "token", "userinfo", "refresh"):
        v = lat.get(name, [])
        if v:
            print(f"{name:10} {len(v):6d} {errors[name]:6d} {statistics.median(v):8.0f} {pct(v, .95):8.0f} {pct(v, .99):8.0f}")
