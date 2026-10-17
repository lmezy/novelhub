"""Tiny in-memory sliding-window rate limiter for sensitive endpoints.

Per-process state is fine here: NovelHub runs a single backend process behind
nginx, and the goal is to blunt credential stuffing / invite-code guessing,
not to enforce billing-grade quotas.
"""

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class SlidingWindowLimiter:
    def __init__(self, max_attempts: int, window_seconds: int):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        """Raise 429 if ``key`` exceeded the limit within the window."""
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            q = self._hits[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_attempts:
                retry = int(q[0] + self.window - now) + 1
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many attempts. Try again in {retry} seconds.",
                )
            q.append(now)
            # Bound memory: drop idle keys when the map grows large.
            if len(self._hits) > 10000:
                for k in [k for k, v in self._hits.items() if not v or v[-1] < cutoff]:
                    del self._hits[k]


    def reset(self) -> None:
        """Clear all tracked attempts (used by the test suite)."""
        with self._lock:
            self._hits.clear()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# Login: 10 attempts per 5 minutes per IP.  Register: 5 per hour per IP.
login_limiter = SlidingWindowLimiter(max_attempts=10, window_seconds=300)
register_limiter = SlidingWindowLimiter(max_attempts=5, window_seconds=3600)
