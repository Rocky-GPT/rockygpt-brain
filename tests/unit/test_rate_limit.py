from rockygpt_brain.security.rate_limit import FixedWindowRateLimiter


def test_allows_up_to_limit_then_blocks() -> None:
    limiter = FixedWindowRateLimiter(limit=3, window_seconds=60)
    for _ in range(3):
        assert limiter.check("a", now=0.0).allowed
    result = limiter.check("a", now=0.0)
    assert not result.allowed
    assert result.retry_after_seconds >= 1


def test_window_resets_after_expiry() -> None:
    limiter = FixedWindowRateLimiter(limit=1, window_seconds=10)
    assert limiter.check("a", now=0.0).allowed
    assert not limiter.check("a", now=5.0).allowed
    assert limiter.check("a", now=10.0).allowed


def test_retry_after_never_understates_remaining_window() -> None:
    limiter = FixedWindowRateLimiter(limit=1, window_seconds=10)
    limiter.check("a", now=0.0)
    # 0.1s elapsed of a 10s window -> 9.9s remain; math.ceil must round up,
    # never down, so a client never retries too early.
    result = limiter.check("a", now=0.1)
    assert result.retry_after_seconds == 10


def test_independent_keys_have_independent_windows() -> None:
    limiter = FixedWindowRateLimiter(limit=1, window_seconds=60)
    assert limiter.check("a", now=0.0).allowed
    assert limiter.check("b", now=0.0).allowed


def test_max_keys_evicts_least_recently_used() -> None:
    limiter = FixedWindowRateLimiter(limit=5, window_seconds=60, max_keys=2)
    limiter.check("a", now=0.0)
    limiter.check("b", now=0.0)
    limiter.check("c", now=0.0)  # evicts "a" (least recently used)
    assert len(limiter._windows) == 2
    assert "a" not in limiter._windows
    assert "b" in limiter._windows
    assert "c" in limiter._windows


def test_max_keys_never_exceeded_under_sustained_high_cardinality() -> None:
    limiter = FixedWindowRateLimiter(limit=5, window_seconds=60, max_keys=100)
    for i in range(10_000):
        limiter.check(f"key-{i}", now=0.0)
    assert len(limiter._windows) <= 100


def test_reusing_a_key_marks_it_recently_used() -> None:
    limiter = FixedWindowRateLimiter(limit=5, window_seconds=60, max_keys=2)
    limiter.check("a", now=0.0)
    limiter.check("b", now=0.0)
    limiter.check("a", now=0.0)  # touch "a" again -> "b" becomes LRU
    limiter.check("c", now=0.0)  # evicts "b", not "a"
    assert "a" in limiter._windows
    assert "b" not in limiter._windows
