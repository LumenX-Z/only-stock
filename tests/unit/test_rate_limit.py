from app.auth.rate_limit import LoginRateLimiter


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_limits_by_ip_and_login_name() -> None:
    clock = _Clock()
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=60, clock=clock)

    assert limiter.check("10.0.0.1", "alice") is None
    limiter.record_failure("10.0.0.1", "alice")
    assert limiter.check("10.0.0.1", "alice") is None
    limiter.record_failure("10.0.0.1", "alice")

    retry_after = limiter.check("10.0.0.1", "alice")
    assert retry_after is not None
    assert retry_after == 60

    assert limiter.check("10.0.0.1", "bob") is not None
    assert limiter.check("10.0.0.2", "alice") is not None


def test_window_expiry_and_reset() -> None:
    clock = _Clock()
    limiter = LoginRateLimiter(max_attempts=1, window_seconds=30, clock=clock)
    limiter.record_failure("10.0.0.1", "alice")

    assert limiter.check("10.0.0.1", "alice") == 30
    clock.advance(30)
    assert limiter.check("10.0.0.1", "alice") is None

    limiter.record_failure("10.0.0.1", "alice")
    limiter.reset("10.0.0.1", "alice")
    assert limiter.check("10.0.0.1", "alice") is None


def test_empty_login_name_only_limits_ip() -> None:
    clock = _Clock()
    limiter = LoginRateLimiter(max_attempts=1, window_seconds=30, clock=clock)
    limiter.record_failure("10.0.0.1", "")

    assert limiter.check("10.0.0.1", "") == 30
    assert limiter.check("10.0.0.2", "") is None
