"""Token-bucket rate limiter (in-memory; move to Redis only if several API processes are needed)."""
import time


class TokenBucket:
    def __init__(self, capacity: int, refill_per_sec: float, clock=time.monotonic):
        self.capacity, self.rate, self.clock = capacity, refill_per_sec, clock
        self._state = {}

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = self.clock()
        tokens, last = self._state.get(key, (float(self.capacity), now))
        tokens = min(self.capacity, tokens + (now - last) * self.rate)
        if tokens >= cost:
            self._state[key] = (tokens - cost, now)
            return True
        self._state[key] = (tokens, now)
        return False
