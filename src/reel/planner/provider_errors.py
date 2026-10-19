"""Classify model-provider failures and describe them honestly.

A provider failure is either *transient* (overloaded, rate limited, a 5xx) --
retrying shortly is the right response -- or *permanent* (bad credentials, an
unknown model string, a malformed request), where retrying only delays the
error. litellm normalizes every provider's errors onto its own exception
classes, so the split is a type check here rather than string matching at each
call site.

Timeouts and connection errors are deliberately NOT transient: REQUEST_TIMEOUT
is sized for slow local generations (see config.py), and a local model that
timed out once will usually time out again -- retrying would multiply a
30-minute wait.
"""
from __future__ import annotations

import time

import litellm

from ..config import RETRY_BACKOFF_MAX_S, RETRY_BACKOFF_S

_TRANSIENT_NAMES = ("ServiceUnavailableError", "RateLimitError", "InternalServerError")
TRANSIENT_ERRORS: tuple[type[BaseException], ...] = tuple(
    cls for cls in (getattr(litellm, n, None) for n in _TRANSIENT_NAMES)
    if isinstance(cls, type)
)


def is_transient(exc: BaseException) -> bool:
    """True when retrying the same call shortly is likely to succeed."""
    return bool(TRANSIENT_ERRORS) and isinstance(exc, TRANSIENT_ERRORS)


def backoff_delay(retry_number: int) -> float:
    """Seconds to wait before the ``retry_number``-th retry (1-based)."""
    return min(RETRY_BACKOFF_S * (2 ** max(retry_number - 1, 0)), RETRY_BACKOFF_MAX_S)


def sleep_before_retry(retry_number: int) -> None:
    """Indirection so tests can patch the wait away."""
    time.sleep(backoff_delay(retry_number))


def describe_model_error(model: str, error: str, *, transient: bool, retries: int) -> str:
    """The RuntimeError message a facade raises when a model call fails.

    Transient failures say so explicitly: the old single message told the user
    to check their API key even when the provider was simply overloaded, which
    sent debugging in exactly the wrong direction.
    """
    if transient:
        tries = f"{retries} retr{'y' if retries == 1 else 'ies'}"
        return (
            f"Model provider for {model!r} is temporarily unavailable (still failing after "
            f"{tries}). This is a provider-side outage or rate limit, not a configuration "
            f"problem -- retrying the same request later is safe. Original error: {error}"
        )
    return (
        f"Call to model {model!r} failed -- check the provider's credentials (API key env "
        f"var, or AWS credentials for bedrock/) and that the model string is valid for that "
        f"provider. Original error: {error}"
    )
