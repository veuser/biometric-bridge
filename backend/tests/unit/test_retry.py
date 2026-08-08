"""Retry policy.

Sleep is injected so the real backoff arithmetic is exercised without the suite
actually waiting 50 seconds.
"""

from __future__ import annotations

import random

import pytest

from app.core.errors import (
    DeviceUnavailableError,
    HrmsAuthenticationError,
    HrmsUnavailableError,
)
from app.utils.retry import RetryPolicy, call_with_retry


class Recorder:
    def __init__(self) -> None:
        self.slept: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)


def test_succeeds_without_sleeping(a_sleep: Recorder | None = None) -> None:
    sleep = Recorder()
    assert call_with_retry(lambda: "ok", sleep=sleep) == "ok"
    assert sleep.slept == []


def test_retries_a_transient_failure_then_succeeds() -> None:
    sleep = Recorder()
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise HrmsUnavailableError("503")
        return "ok"

    assert call_with_retry(flaky, sleep=sleep) == "ok"
    assert attempts["n"] == 3
    assert len(sleep.slept) == 2


def test_non_retryable_error_fails_immediately() -> None:
    """Retrying a rejected API key just burns the sync window."""
    sleep = Recorder()
    attempts = {"n": 0}

    def bad_key() -> str:
        attempts["n"] += 1
        raise HrmsAuthenticationError("401")

    with pytest.raises(HrmsAuthenticationError):
        call_with_retry(bad_key, sleep=sleep)
    assert attempts["n"] == 1
    assert sleep.slept == []


def test_exhausting_retries_reraises_the_last_error() -> None:
    sleep = Recorder()

    def always_down() -> str:
        raise DeviceUnavailableError("terminal is off")

    with pytest.raises(DeviceUnavailableError, match="terminal is off"):
        call_with_retry(always_down, sleep=sleep)
    assert len(sleep.slept) == 3  # 3 retries after the first attempt


def test_backoff_follows_the_configured_schedule() -> None:
    sleep = Recorder()
    policy = RetryPolicy(delays_seconds=(5.0, 15.0, 30.0), jitter_fraction=0.0)

    with pytest.raises(HrmsUnavailableError):
        call_with_retry(
            lambda: (_ for _ in ()).throw(HrmsUnavailableError("down")),
            policy=policy,
            sleep=sleep,
        )
    assert sleep.slept == [5.0, 15.0, 30.0]


def test_jitter_stays_within_bounds() -> None:
    """Several devices failing together must not retry in lockstep."""
    policy = RetryPolicy(delays_seconds=(10.0,), jitter_fraction=0.2)
    rng = random.Random(1234)
    samples = [policy.delay_for(0, rng) for _ in range(200)]
    assert all(8.0 <= s <= 12.0 for s in samples)
    assert len(set(samples)) > 1, "jitter should actually vary the delay"


def test_jitter_never_produces_a_negative_delay() -> None:
    policy = RetryPolicy(delays_seconds=(0.1,), jitter_fraction=5.0)
    rng = random.Random(7)
    assert all(policy.delay_for(0, rng) >= 0 for _ in range(100))


def test_max_attempts_matches_the_schedule_length() -> None:
    assert RetryPolicy(delays_seconds=(1.0, 2.0)).max_attempts == 3


def test_a_policy_with_no_retries_is_a_single_attempt() -> None:
    sleep = Recorder()
    with pytest.raises(HrmsUnavailableError):
        call_with_retry(
            lambda: (_ for _ in ()).throw(HrmsUnavailableError("down")),
            policy=RetryPolicy(delays_seconds=()),
            sleep=sleep,
        )
    assert sleep.slept == []
