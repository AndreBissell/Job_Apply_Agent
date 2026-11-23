"""Which jobs a worker is quick-screening, extracting or matching right now.

docs/second-worker-plan.md D2/D3. The scoring loop and ``/regenerate`` (on the letter
worker) can reach the same job at the same time. Whoever works on a job holds its claim:
the scorer SKIPS a claimed job (``try_claim``) and moves on to the next one, the user's
``/regenerate`` WAITS for it (``wait_claim``). The order is always: claim, re-read the job,
do the work, commit, then ``release`` in a ``finally`` (the ``claim`` context manager
does the first and the last).

One meaning only. ``letter_bands.queued`` ("a letter is being written") and
``quickscreen._in_flight`` (which ``quick_screen`` takes itself) are separate and unchanged.

In memory, because the app is one process: every thread sees the same set, and a crash
kills every holder, so forgetting the claims on a restart is correct. Several processes
(hosting) would need a DB lease or Postgres ``SELECT ... FOR UPDATE SKIP LOCKED`` instead.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

_changed = threading.Condition()
_held: dict[int, float] = {}  # job id -> time.monotonic() when it was claimed


def try_claim(job_id: int) -> bool:
    """Claim ``job_id`` if nobody holds it. False means another worker has it: skip it."""
    with _changed:
        if job_id in _held:
            return False
        _held[job_id] = time.monotonic()
        return True


def wait_claim(job_id: int, timeout: float) -> bool:
    """Wait up to ``timeout`` seconds for ``job_id`` to be free, then claim it.

    True: the caller now holds the claim and must ``release`` it. False: it was still
    held when the time ran out, and the caller does NOT hold it.
    """
    with _changed:
        if not _changed.wait_for(lambda: job_id not in _held, timeout=timeout):
            return False
        _held[job_id] = time.monotonic()
        return True


def release(job_id: int) -> None:
    """Give up the claim on ``job_id`` and wake anyone waiting for it. Releasing a job
    nobody holds does nothing. Only the holder calls this."""
    with _changed:
        if _held.pop(job_id, None) is not None:
            _changed.notify_all()


@contextmanager
def claim(job_id: int, wait: float | None = None) -> Iterator[bool]:
    """``with claim(job_id) as claimed:`` takes the claim for the block and releases it on
    the way out, also when the block raises. Yields whether the claim was taken.

    ``wait=None`` tries once (``try_claim``, the scorer); a number waits that many seconds
    (``wait_claim``, ``/regenerate``). A claim that was not taken is never released, so a
    caller that goes ahead without it cannot free the holder's claim.
    """
    claimed = try_claim(job_id) if wait is None else wait_claim(job_id, wait)
    try:
        yield claimed
    finally:
        if claimed:
            release(job_id)


def held_ids() -> frozenset[int]:
    """The jobs claimed right now, for the scorer's candidate queries to leave out."""
    with _changed:
        return frozenset(_held)


def held_for(job_id: int) -> float | None:
    """Seconds ``job_id`` has been claimed, or None when nobody holds it."""
    with _changed:
        since = _held.get(job_id)
    return None if since is None else time.monotonic() - since
