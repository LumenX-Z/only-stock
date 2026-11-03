"""登录失败限流。

依据 docs/04-认证、会话与凭证安全.md 5.4：登录接口按 IP 与登录名限流。
应用为单 worker，限流状态保存在进程内；窗口内失败次数达到上限后返回 429。
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable


class LoginRateLimiter:
    def __init__(
        self,
        *,
        max_attempts: int = 5,
        window_seconds: int = 900,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts 必须大于等于 1")
        if window_seconds < 1:
            raise ValueError("window_seconds 必须大于等于 1")
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._clock = clock or time.monotonic
        self._attempts: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, ip: str, login_name: str) -> int | None:
        """返回需要等待的秒数；未触发限流时返回 None。"""
        now = self._clock()
        with self._lock:
            limits = [self._retry_after(self._ip_key(ip), now)]
            if login_name:
                limits.append(self._retry_after(self._name_key(login_name), now))
        delays = [delay for delay in limits if delay is not None]
        return max(delays) if delays else None

    def record_failure(self, ip: str, login_name: str) -> None:
        now = self._clock()
        with self._lock:
            self._append(self._ip_key(ip), now)
            if login_name:
                self._append(self._name_key(login_name), now)

    def reset(self, ip: str, login_name: str) -> None:
        with self._lock:
            self._attempts.pop(self._ip_key(ip), None)
            if login_name:
                self._attempts.pop(self._name_key(login_name), None)

    def _append(self, key: str, now: float) -> None:
        events = self._attempts.setdefault(key, deque())
        self._prune(events, now)
        events.append(now)

    def _retry_after(self, key: str, now: float) -> int | None:
        events = self._attempts.get(key)
        if not events:
            return None
        self._prune(events, now)
        if len(events) < self._max_attempts:
            return None
        return max(1, math.ceil(self._window_seconds - (now - events[0])))

    def _prune(self, events: deque[float], now: float) -> None:
        cutoff = now - self._window_seconds
        while events and events[0] <= cutoff:
            events.popleft()

    @staticmethod
    def _ip_key(ip: str) -> str:
        return f"ip:{ip}"

    @staticmethod
    def _name_key(login_name: str) -> str:
        return f"name:{login_name}"
