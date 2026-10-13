"""Two overlapping quick_screen() calls for the SAME job must make one model call.

The sidebar's Scan Page calls POST /jobs/{id}/quick-screen on a request thread, while
the idle loop's Phase 0 may pick the same just-ingested job on the worker thread. Both
used to pass the "already screened?" check before either committed, so the model was
paid for twice and, on a skip, both added a Match row: the second commit broke
UNIQUE(user_id, job_id) with an IntegrityError.

A FILE database in tmp_path (not :memory: + StaticPool, which shares one connection
across threads and would hide the bug). No LLM: complete_json is replaced by a fake
that counts calls and holds the first one open. Every wait has a timeout.
"""
from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.llm.quickscreen as quickscreen_module
from app.db import Base
from app.models import JobListing, Match, Profile


@pytest.fixture()
def factory(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'race.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    f = sessionmaker(bind=eng)
    with f() as s:
        s.add(Profile(id=1, name="Bob", email="b@x.com", password_hash="x"))
        s.add(JobListing(id=5, source="seek", source_job_id="5", url="u5", title="Chef",
                         raw_description="We cook food."))
        s.commit()
    yield f
    eng.dispose()


def test_overlapping_screens_of_one_job_call_the_model_once(factory, monkeypatch):
    calls: list[int] = []
    lock = threading.Lock()
    entered = threading.Event()
    release = threading.Event()

    def fake(*a, **k):
        with lock:
            calls.append(1)
        entered.set()
        release.wait(timeout=5)
        return {"score": 10, "reason": "x"}  # below the skip threshold

    monkeypatch.setattr(quickscreen_module, "complete_json", fake)

    results: dict[str, object] = {}

    def run(name: str) -> None:
        try:
            with factory() as s:
                results[name] = quickscreen_module.quick_screen(5, 1, session=s)
        except Exception as exc:  # noqa: BLE001 — the test reports it below
            results[name] = exc

    t1 = threading.Thread(target=run, args=("first",))
    t1.start()
    assert entered.wait(timeout=5), "first call never reached the model"

    t2 = threading.Thread(target=run, args=("second",))
    t2.start()
    time.sleep(0.5)  # let the second call either return or reach the model
    release.set()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not t1.is_alive() and not t2.is_alive()

    assert results["second"] == "not-run", results
    assert results["first"] == "skipped", results
    assert len(calls) == 1
    with factory() as s:
        assert len(s.scalars(select(Match).where(Match.job_id == 5)).all()) == 1
