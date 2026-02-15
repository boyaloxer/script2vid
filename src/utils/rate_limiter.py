"""
Sliding-window rate limiter for API calls.

Tracks request timestamps and automatically pauses when approaching the
rate limit, resuming once the window rolls over. Designed for the Pexels
API (200 requests/hour) but generic enough for any API.
"""

import time
from collections import deque


class RateLimiter:
    """
    Sliding-window rate limiter.

    Args:
        max_requests: Maximum requests allowed in the window.
        window_seconds: Length of the sliding window in seconds.
        headroom: Stop this many requests before the hard limit (safety buffer).
        name: Label for log messages (e.g., "Pexels").
    """

    def __init__(
        self,
        max_requests: int = 200,
        window_seconds: int = 3600,
        headroom: int = 20,
        name: str = "API",
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.effective_limit = max_requests - headroom
        self.name = name
        self._timestamps: deque[float] = deque()

    def _purge_old(self):
        """Remove timestamps outside the current sliding window."""
        cutoff = time.time() - self.window_seconds
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    def wait_if_needed(self):
        """
        Block until it's safe to make another request.
        If we're at the limit, prints a message and sleeps until the oldest
        request falls outside the window.
        """
        self._purge_old()

        if len(self._timestamps) >= self.effective_limit:
            # Calculate how long until the oldest request expires
            oldest = self._timestamps[0]
            wait_until = oldest + self.window_seconds
            wait_seconds = wait_until - time.time()

            if wait_seconds > 0:
                minutes = int(wait_seconds // 60)
                seconds = int(wait_seconds % 60)
                print(
                    f"[Rate Limiter] {self.name}: "
                    f"Reached {len(self._timestamps)}/{self.max_requests} requests. "
                    f"Pausing for {minutes}m {seconds}s until window resets..."
                )
                time.sleep(wait_seconds + 1)  # +1s buffer
                self._purge_old()

    def record(self):
        """Record that a request was just made."""
        self._timestamps.append(time.time())

    @property
    def requests_remaining(self) -> int:
        """How many more requests can be made before hitting the limit."""
        self._purge_old()
        return max(0, self.effective_limit - len(self._timestamps))

    @property
    def requests_used(self) -> int:
        """How many requests have been made in the current window."""
        self._purge_old()
        return len(self._timestamps)
