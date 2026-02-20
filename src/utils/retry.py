"""
Retry decorator with exponential backoff for transient API failures.
"""

import time
import functools


def retry(
    max_attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    exceptions: tuple = (Exception,),
    on_retry=None,
):
    """
    Retry a function on failure with exponential backoff.

    Args:
        max_attempts: Total attempts (including the first).
        base_delay: Initial delay between retries (seconds).
        max_delay: Maximum delay cap (seconds).
        exceptions: Tuple of exception types to catch.
        on_retry: Optional callback(attempt, exception) called before each retry.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == max_attempts:
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    if on_retry:
                        on_retry(attempt, e)
                    else:
                        print(
                            f"[Retry] {func.__name__} failed (attempt {attempt}/{max_attempts}): "
                            f"{e}. Retrying in {delay:.0f}s..."
                        )
                    time.sleep(delay)
            raise last_exc
        return wrapper
    return decorator
