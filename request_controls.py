import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Request


RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_MAX_REQUESTS = 30
TRUSTED_PROXY_IP = "127.0.0.1"

_rate_lock = Lock()
_rate_events: dict[str, deque[float]] = defaultdict(deque)


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    if peer == TRUSTED_PROXY_IP:
        forwarded = request.headers.get("x-forwarded-for", "")
        candidate = forwarded.strip()
        if candidate and "," not in candidate:
            return candidate
    return peer or "unknown"


def allow_request(ip: str) -> bool:
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    with _rate_lock:
        events = _rate_events[ip]
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= RATE_LIMIT_MAX_REQUESTS:
            return False
        events.append(now)
        return True


def reset_rate_limits() -> None:
    with _rate_lock:
        _rate_events.clear()
