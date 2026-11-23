"""app/job_claims.py: the in-memory claims on jobs being scored (docs/second-worker-plan.md D2).

Pure Python: no database, no server, no AI call. Every wait is bounded by WAIT_S and
driven by threading.Event, so a failure fails fast instead of hanging.
"""

from __future__ import annotations

import threading
import time

import pytest

from app import job_claims

WAIT_S = 10


@pytest.fixture(autouse=True)
def _no_leftover_claims():
    job_claims._held.clear()
    yield
    job_claims._held.clear()


def _in_thread(fn) -> tuple[threading.Thread, dict]:
    out: dict = {}

    def run():
        out["result"] = fn()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out


class TestTryClaim:
    def test_a_second_claim_on_the_same_job_is_refused(self):
        assert job_claims.try_claim(5) is True
        assert job_claims.try_claim(5) is False

    def test_another_jobs_claim_is_unaffected(self):
        assert job_claims.try_claim(5) is True
        assert job_claims.try_claim(6) is True
        assert job_claims.held_ids() == {5, 6}

    def test_release_then_claim_succeeds(self):
        job_claims.try_claim(5)
        job_claims.release(5)
        assert job_claims.held_ids() == frozenset()
        assert job_claims.try_claim(5) is True

    def test_releasing_an_unclaimed_job_is_harmless(self):
        job_claims.try_claim(6)
        job_claims.release(5)
        assert job_claims.held_ids() == {6}


class TestWaitClaim:
    def test_a_free_job_is_claimed_at_once(self):
        assert job_claims.wait_claim(5, timeout=0) is True
        assert job_claims.try_claim(5) is False  # now held by the waiter

    def test_it_returns_true_as_soon_as_the_holder_releases(self):
        job_claims.try_claim(5)
        waiting = threading.Event()

        def wait():
            waiting.set()
            return job_claims.wait_claim(5, timeout=WAIT_S)

        t, out = _in_thread(wait)
        assert waiting.wait(WAIT_S)
        assert t.is_alive()  # still waiting: the job is held
        started = time.monotonic()
        job_claims.release(5)
        t.join(WAIT_S)

        assert not t.is_alive() and out["result"] is True
        assert time.monotonic() - started < WAIT_S / 2  # woken by the release, not the timeout
        assert job_claims.held_ids() == {5}  # the waiter holds it now

    def test_it_returns_false_after_its_timeout_when_nobody_releases(self):
        job_claims.try_claim(5)
        started = time.monotonic()
        assert job_claims.wait_claim(5, timeout=0.2) is False
        assert time.monotonic() - started >= 0.2
        assert job_claims.held_ids() == {5}  # the holder still has it

    def test_another_jobs_release_does_not_end_the_wait(self):
        job_claims.try_claim(5)
        job_claims.try_claim(6)
        job_claims.release(6)
        assert job_claims.wait_claim(5, timeout=0.1) is False


class TestClaimContextManager:
    def test_the_claim_is_held_inside_and_released_after(self):
        with job_claims.claim(5) as claimed:
            assert claimed is True
            assert job_claims.held_ids() == {5}
        assert job_claims.held_ids() == frozenset()

    def test_the_claim_is_released_when_the_work_raises(self):
        with pytest.raises(RuntimeError):
            with job_claims.claim(5):
                raise RuntimeError("the model call failed")
        assert job_claims.try_claim(5) is True

    def test_a_claim_not_taken_never_frees_the_holders_claim(self):
        job_claims.try_claim(5)
        with job_claims.claim(5) as claimed:
            assert claimed is False
        assert job_claims.held_ids() == {5}

    def test_with_wait_it_waits_for_the_holder(self):
        job_claims.try_claim(5)
        inside = threading.Event()

        def wait():
            with job_claims.claim(5, wait=WAIT_S) as claimed:
                inside.set()
                return claimed

        t, out = _in_thread(wait)
        assert not inside.wait(0.1)  # blocked behind the holder
        job_claims.release(5)
        assert inside.wait(WAIT_S)
        t.join(WAIT_S)
        assert out["result"] is True
        assert job_claims.held_ids() == frozenset()

    def test_with_wait_a_timeout_yields_false_and_keeps_the_holders_claim(self):
        job_claims.try_claim(5)
        with job_claims.claim(5, wait=0.05) as claimed:
            assert claimed is False
        assert job_claims.held_ids() == {5}


class TestHeldFor:
    def test_none_when_nobody_holds_the_job(self):
        assert job_claims.held_for(5) is None

    def test_seconds_since_the_claim(self):
        before = time.monotonic()
        job_claims.try_claim(5)
        assert job_claims.wait_claim(5, timeout=0.05) is False  # let some time pass
        held = job_claims.held_for(5)
        assert 0.05 <= held <= time.monotonic() - before
