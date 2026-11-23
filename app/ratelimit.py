"""A small in-memory sliding-window rate limiter.

Per process only: with several API instances each keeps its own counts. Behind a load balancer you would
use a shared store (Redis, DynamoDB) or the gateway's own limiter. The idea and the tests carry over."""
import threading
import time
from collections import deque

MAX_KEYS = 10_000   # bound memory: past this many tracked keys, stale ones are swept


class RateLimiter:
    def __init__(self, max_events: int, window_seconds: float, clock=time.monotonic):
        self.max_events, self.window, self.clock = max_events, window_seconds, clock
        self._events: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _live(self, key, now):
        """The key's events inside the window (expired ones dropped), or None if it has none."""
        q = self._events.get(key)
        if q is None:
            return None
        while q and q[0] <= now - self.window:
            q.popleft()
        if not q:
            del self._events[key]
            return None
        return q

    def _sweep(self, now):
        for key in list(self._events):
            self._live(key, now)

    def retry_after(self, key: str) -> float:
        """0 if the key is free, otherwise the seconds until it will be. Does not count as an event."""
        with self._lock:
            now = self.clock()
            q = self._live(key, now)
            if q is None or len(q) < self.max_events:
                return 0.0
            return q[0] + self.window - now

    def record(self, key: str) -> None:
        with self._lock:
            now = self.clock()
            if len(self._events) > MAX_KEYS:
                self._sweep(now)
            self._events.setdefault(key, deque()).append(now)

    def acquire(self, key: str) -> float:
        """Check and count in one step. 0 means allowed (and counted); otherwise seconds to wait."""
        with self._lock:
            now = self.clock()
            q = self._live(key, now)
            if q is not None and len(q) >= self.max_events:
                return q[0] + self.window - now
            if len(self._events) > MAX_KEYS:
                self._sweep(now)
            self._events.setdefault(key, deque()).append(now)
            return 0.0

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()