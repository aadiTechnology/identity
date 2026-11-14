"""Off-server uptime check for every public Aadi service. Exit code 1 if any check fails (the scheduled workflow then turns red and GitHub emails the
repository's watchers). Uses only the standard library. Usage: python ops/uptime_check.py [extra_url ...]"""
import sys
import time
import urllib.request

CHECKS = [
    ("Identity ready", "https://id.aadiprojecttracker.aaditechnology.com/health/ready"),
    ("Project Tracker", "https://aadiprojecttracker.aaditechnology.com/api/health"),
    ("Academics", "https://academics.aaditechnology.com/health"),
    ("Project CRM", "https://crm.aaditechnology.com/api/v1/health"),
    ("DMS", "https://dms.aaditechnology.com/api/v1/health"),
]


def check(url: str, tries: int = 3, timeout: float = 15.0) -> tuple[bool, str]:
    last = ""
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "aadi-uptime-check"}), timeout=timeout) as r:
                if r.status == 200:
                    return True, "200"
                last = str(r.status)
        except Exception as e:  # noqa: BLE001
            last = type(e).__name__ + ": " + str(e)[:80]
        time.sleep(2 * (i + 1))
    return False, last


def main(extra: list[str]) -> int:
    failed = 0
    for name, url in CHECKS + [(u, u) for u in extra]:
        ok, detail = check(url)
        print(("PASS " if ok else "FAIL ") + f"{name}  {url}  {detail}")
        failed += not ok
    print(f"{failed} failed" if failed else "all healthy")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
