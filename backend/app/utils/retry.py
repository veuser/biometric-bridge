"""Retry with exponential backoff.

Only errors that declare themselves retryable are retried, so the policy lives in
the error taxonomy rather than in scattered ``isinstance`` checks. Retrying a bad
API key or a malformed payload just burns the sync window.

Jitter matters even with a single bridge: several devices whose syncs fail at the
same moment (an HRMS outage) would otherwise retry in lockstep and hammer the
service as it comes back.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from app.core.errors import BridgeError
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    #: Delay before each retry. Length also sets the number of retries.
    delays_seconds: tuple[float, ...] = (5.0, 15.0, 30.0)
    jitter_fraction: float = 0.2

    @property
    def max_attempts(self) -> int:
        return len(self.delays_seconds) + 1

    def delay_for(self, retry_index: int, rng: random.Random | None = None) -> float:
        base = self.delays_seconds[retry_index]
        if self.jitter_fraction <= 0:
            return base
        generator = rng or random
        spread = base * self.jitter_fraction
        return max(0.0, base + generator.uniform(-spread, spread))


DEFAULT_RETRY_POLICY = RetryPolicy()


def call_with_retry(
    operation: Callable[[], T],
    *,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    description: str = "operation",
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> T:
    """Run ``operation``, retrying transient :class:`BridgeError`s.

    ``sleep`` is injectable so tests exercise the real backoff logic without
    actually waiting 50 seconds.
    """
    last_error: BridgeError | None = None

    for attempt in range(policy.max_attempts):
        try:
            return operation()
        except BridgeError as exc:
            if not exc.retryable:
                # Deterministic failure — another attempt produces the same result.
                raise
            last_error = exc
            if attempt == policy.max_attempts - 1:
                break
            delay = policy.delay_for(attempt, rng)
            logger.warning(
                "%s failed; retrying.",
                description,
                extra={
                    "attempt": attempt + 1,
                    "max_attempts": policy.max_attempts,
                    "retry_in_seconds": round(delay, 1),
                    "error": exc.message,
                },
            )
            sleep(delay)

    assert last_error is not None
    logger.error(
        "%s failed after exhausting retries.",
        description,
        extra={"attempts": policy.max_attempts, "error": last_error.message},
    )
    raise last_error
