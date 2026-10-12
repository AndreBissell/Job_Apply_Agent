"""_process_listing stops when the whole account is stopped (ARCHITECTURE 5.4 #11).

A daily-quota or budget stop applies to every later step too, so continuing only makes
more doomed requests and the sidebar never hears about it. Ordinary failures stay
per-job: a bad extraction still lets the match run.

No AI call is possible here: extract_job, match_job, the letter step, the database
session and the broadcast are all replaced with fakes that record their calls, so
nothing reaches app/llm/client.py.
"""

from __future__ import annotations

import pytest

import app.api.main as api
from app.api import events
from app.llm.client import BudgetExceededError, DailyQuotaError


class _FakeSession:
    def __enter__(self):
        return object()

    def __exit__(self, *exc):
        return False


@pytest.fixture
def calls(monkeypatch):
    log: dict[str, list] = {"extract": [], "match": [], "letter": [], "broadcast": []}

    def make(name, raises=None):
        def fake(*args, **kwargs):
            log[name].append(args)
            if raises is not None:
                raise raises
            return None
        return fake

    def install(extract_raises=None, match_raises=None):
        monkeypatch.setattr(api, "extract_job", make("extract", extract_raises))
        monkeypatch.setattr(api, "match_job", make("match", match_raises))
        monkeypatch.setattr(api.letter_production, "generate_for", make("letter"))

    monkeypatch.setattr(api, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(events, "broadcast_from_thread", lambda event, data: log["broadcast"].append((event, data)))
    log["install"] = install  # type: ignore[assignment]
    return log


def _blocked(log):
    return [b for b in log["broadcast"] if b[0] == "llm_budget_blocked"]


def test_quota_stop_in_extract_skips_match_and_letter(calls):
    calls["install"](extract_raises=DailyQuotaError("daily quota used up"))
    api._process_listing(1, 1, has_description=True, with_cover_letter=True)
    assert calls["match"] == []
    assert calls["letter"] == []
    assert len(_blocked(calls)) == 1
    assert "daily quota" in _blocked(calls)[0][1]["reason"]


def test_budget_stop_in_match_skips_letter(calls):
    calls["install"](match_raises=BudgetExceededError("budget reached"))
    api._process_listing(1, 1, has_description=True, with_cover_letter=True)
    assert len(calls["match"]) == 1
    assert calls["letter"] == []
    assert len(_blocked(calls)) == 1
    # a stopped match is not a processed job
    assert not [b for b in calls["broadcast"] if b[0] == "job_processed"]


def test_ordinary_extract_failure_still_matches(calls):
    calls["install"](extract_raises=ValueError("bad json"))
    api._process_listing(1, 1, has_description=True, with_cover_letter=True)
    assert len(calls["match"]) == 1
    assert len(calls["letter"]) == 1
    assert _blocked(calls) == []


def test_card_only_listing_does_nothing(calls):
    calls["install"]()
    api._process_listing(1, 1, has_description=False, with_cover_letter=True)
    assert calls["extract"] == [] and calls["match"] == [] and calls["letter"] == []
    assert calls["broadcast"] == []
