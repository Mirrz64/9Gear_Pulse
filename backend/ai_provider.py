"""Shared retry-with-backoff wrapper for Anthropic API calls, used by
generate_pipeline.py, propose_architecture.py, and
generate_scaffold_file.py - all three previously duplicated the exact
same two-provider (Anthropic-then-OpenAI) structure independently.

A rate limit (429) or server overload (529) is an expected, recoverable
condition - not a reason to immediately downgrade to a different,
weaker model. Under real concurrent usage, hitting these routinely is
normal, not exceptional; falling straight through to OpenAI on every
momentary blip would mean silently degrading real customers' output
quality the moment traffic is high enough to matter, rather than
briefly waiting the short time it actually takes for capacity to free
up. A genuinely broken key, an actual outage, or any other failure
still falls through to the OpenAI fallback immediately, exactly as
before - this only changes what happens for the specific, recoverable
429/529 case.

The Anthropic SDK already retries a 429 once or twice internally by
default (its own max_retries, unset here so it keeps that default) -
by the time RateLimitError/APIStatusError(529) actually reaches this
code, that inner layer has already been exhausted. This is the correct
outer layer on top of it, not a duplicate of what the SDK already does.
"""
import random
import time
from typing import Callable, TypeVar

from anthropic import APIStatusError, RateLimitError

T = TypeVar("T")

_DEFAULT_MAX_RETRIES = 3
_BASE_DELAY_SECONDS = 1.0
_MAX_DELAY_SECONDS = 30.0


def call_with_rate_limit_backoff(fn: Callable[[], T], max_retries: int = _DEFAULT_MAX_RETRIES) -> T:
    """Calls fn() (a zero-argument callable wrapping one Anthropic API
    call) and retries with backoff specifically on RateLimitError (429)
    or a 529 (overloaded) APIStatusError. Any other exception - a bad
    key, a genuine outage, anything else - propagates immediately and
    unchanged, exactly as before this existed, so the caller's existing
    except-then-fall-back-to-OpenAI logic still handles those cases
    exactly as it always did.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return fn()
        except RateLimitError as exc:
            last_exc = exc
            # The response itself tells us how long to actually wait,
            # rather than guessing - fall back to a base delay only if
            # that header is genuinely absent.
            try:
                retry_after = float(exc.response.headers.get("retry-after", _BASE_DELAY_SECONDS))
            except Exception:
                retry_after = _BASE_DELAY_SECONDS
            delay = min(retry_after + random.uniform(0, 1), _MAX_DELAY_SECONDS)
            print(f"[AI Provider] Rate-limited (attempt {attempt + 1}/{max_retries}). Waiting {delay:.1f}s before retrying...")
            time.sleep(delay)
        except APIStatusError as exc:
            if exc.status_code != 529:
                raise
            last_exc = exc
            delay = min(_BASE_DELAY_SECONDS * (2 ** attempt) + random.uniform(0, 1), _MAX_DELAY_SECONDS)
            print(f"[AI Provider] Anthropic overloaded (attempt {attempt + 1}/{max_retries}). Waiting {delay:.1f}s before retrying...")
            time.sleep(delay)
    # Every retry attempt hit a 429/529 - give up and let the caller's
    # existing except block fall through to the OpenAI fallback, same
    # as any other Anthropic failure always has.
    raise last_exc
