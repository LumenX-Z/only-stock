"""MCP 按 Key 与来源 IP 的限流。

依据 docs/04-认证、会话与凭证安全.md 5.5 第 9 条：默认每把 Key 每分钟 60 次、
每来源 IP 每分钟 120 次，超限返回 429。应用为单 worker，滑动窗口状态保存在
进程内；状态只保存计数时间戳，不保存完整凭证。
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable

RATE_WINDOW_SECONDS = 60
_MAX_TRACKED_KEYS = 10_000


class MCPRateLimiter:
    """滑动窗口限流器：分别按来源 IP 与 Key ID 统计请求。"""

    def __init__(
        self,
        *,
        key_per_minute: int,
        ip_per_minute: int,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if key_per_minute < 1 or ip_per_minute < 1:
            raise ValueError("限流阈值必须大于等于 1")
        self._key_limit = key_per_minute
        self._ip_limit = ip_per_minute
        self._clock = clock or time.monotonic
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check_and_record_ip(self, ip: str) -> int | None:
        """记录一次请求；超限返回需要等待的秒数。"""
        return self._hit(f"ip:{ip}", self._ip_limit)

    def check_and_record_key(self, api_key_id: str) -> int | None:
        """记录一次已认证请求；超限返回需要等待的秒数。"""
        return self._hit(f"key:{api_key_id}", self._key_limit)

    def _hit(self, bucket: str, limit: int) -> int | None:
        now = self._clock()
        with self._lock:
            self._prune_all(now)
            events = self._events.setdefault(bucket, deque())
            if len(events) >= limit:
                return max(1, math.ceil(RATE_WINDOW_SECONDS - (now - events[0])))
            events.append(now)
            return None

    def _prune_all(self, now: float) -> None:
        if len(self._events) > _MAX_TRACKED_KEYS:
            self._events = {
                bucket: events
                for bucket, events in self._events.items()
                if events and events[-1] > now - RATE_WINDOW_SECONDS
            }
        cutoff = now - RATE_WINDOW_SECONDS
        for bucket in list(self._events):
            events = self._events[bucket]
            while events and events[0] <= cutoff:
                events.popleft()
            if not events:
                del self._events[bucket]
