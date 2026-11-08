"""Small in-process sliding-window limiter. Enough for one server; move to a shared store (Redis) before running several."""
import time
from collections import defaultdict, deque

_hits: dict[str, deque] = defaultdict(deque)


def hit(key: str, limit: int, window: float = 60.0) -> bool:
    """Records an attempt and returns True when the caller is over the limit."""
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > window:
        q.popleft()
    q.append(now)
    if not q and key in _hits:
        del _hits[key]
    return len(q) > limit


def reset():
    _hits.clear()
