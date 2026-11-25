"""
auth/throttle.py

Slows down repeated failed sign-ins. Each key (an email, a client address)
gets a few free failures; after that it must wait before the next attempt,
twice as long after each new failure, up to 15 minutes. A quiet period of
15 minutes forgets the failures. A blocked attempt answers 429 with
Retry-After instead of sleeping, so no worker is held.

The state lives in the API process. RADA runs one uvicorn process; with
several, each would count separately (the limits would be multiplied by the
number of processes, not lifted).
"""

import threading
import time
from dataclasses import dataclass

MAX_TRACKED_KEYS = 10_000


@dataclass
class _Entry:
    failures: int = 0
    last_failure: float = 0.0
    blocked_until: float = 0.0


class LoginThrottle:
    def __init__(self, free_failures: int, base_delay: float = 1.0, max_delay: float = 900.0,
                 forget_after: float = 900.0, clock=time.monotonic):
        self.free_failures = free_failures
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.forget_after = forget_after
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> float:
        """Seconds to wait before the next attempt for this key; 0 if allowed now."""
        with self._lock:
            entry = self._current(key)
            return max(0.0, entry.blocked_until - self._clock()) if entry else 0.0

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            entry = self._current(key)
            if entry is None:
                if len(self._entries) >= MAX_TRACKED_KEYS:
                    self._prune(now)
                entry = self._entries[key] = _Entry()
            entry.failures += 1
            entry.last_failure = now
            extra = entry.failures - self.free_failures
            if extra > 0:
                entry.blocked_until = now + min(self.max_delay, self.base_delay * 2 ** (extra - 1))

    def record_success(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()

    def _current(self, key: str) -> _Entry | None:
        entry = self._entries.get(key)
        if entry and self._clock() - entry.last_failure > max(self.forget_after, entry.blocked_until - entry.last_failure):
            del self._entries[key]
            return None
        return entry

    def _prune(self, now: float) -> None:
        for key in [k for k, e in self._entries.items() if now - e.last_failure > self.forget_after]:
            del self._entries[key]


# Per account: 5 free failures. Per client address: 30, for password spraying
# across many accounts (needs uvicorn --proxy-headers behind Traefik, see docs/AUTH.md).
by_email = LoginThrottle(free_failures=5)
by_client = LoginThrottle(free_failures=30)


def reset_all() -> None:
    by_email.reset()
    by_client.reset()
