"""Tests for tiers, usage logging, the budget guard and tool calling in
app/llm/client.py (+ app/llm/usage.py and GET /llm/usage).

No network: the provider call (``_generate_gemini``) is replaced with a fake that
returns an object shaped like a Gemini response, and the usage/budget DB is an
in-memory SQLite patched in via ``client._usage_session``.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app, get_db
from app.db import Base
from app.llm import client, usage
from app.llm.client import BudgetExceededError, LLMError, ToolSpec, Usage
from app.models import LlmUsage, Profile


@pytest.fixture()
def Session():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _fk_on(conn, _):
        if isinstance(conn, sqlite3.Connection):
            conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    yield sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    Base.metadata.drop_all(eng)


@pytest.fixture(autouse=True)
def _never_touch_the_real_db(monkeypatch):
    """Default the usage/budget DB to "unavailable" so a test that forgets the
    ``db`` fixture can't write rows into the dev database (the guard fails open
    and logging swallows the error). ``db`` below overrides this."""

    def unavailable():
        raise RuntimeError("usage DB not patched in this test")

    monkeypatch.setattr(client, "_usage_session", unavailable)


@pytest.fixture()
def db(Session, monkeypatch):
    """A session on the in-memory DB, also used by the client's usage logging."""
    monkeypatch.setattr(client, "_usage_session", lambda: Session())
    with Session() as s:
        s.add(Profile(id=1, name="Bob", email="bob@example.com", password_hash="x"))
        s.commit()
        yield s


def _spend(db, cost: float, *, when: datetime.datetime | None = None, tier="strong"):
    db.add(
        LlmUsage(
            task="t", tier=tier, model="m", cost_usd=cost,
            created_at=when or datetime.datetime.now(datetime.timezone.utc),
        )
    )
    db.commit()


def _set_prefs(db, **prefs):
    db.get(Profile, 1).preferences = json.dumps(prefs)
    db.commit()


# ---------------------------------------------------------------------------
# Tiers -> models
# ---------------------------------------------------------------------------
def test_model_for_each_provider():
    assert client.model_for("small", "gemini") == client.GEMINI_MODEL_SMALL
    assert client.model_for("mid", "gemini") == client.GEMINI_MODEL_MID
    assert client.model_for("strong", "gemini") == client.GEMINI_MODEL_STRONG
    assert client.model_for("small", "openai") == client.OPENAI_MODEL_SMALL
    # OpenAI has one better model, shared by mid and strong.
    assert client.model_for("mid", "openai") == client.OPENAI_MODEL_LETTER
    assert client.model_for("strong", "openai") == client.OPENAI_MODEL_LETTER
    assert client.model_for("strong", "groq") == client.GROQ_MODEL


def test_unknown_tier_and_provider_raise():
    with pytest.raises(LLMError, match="Unknown tier"):
        client.model_for("huge", "gemini")
    with pytest.raises(LLMError, match="Unknown LLM_PROVIDER"):
        client.model_for("small", "nope")


def test_default_tiers_keep_old_behaviour():
    """Structured work stays cheap; prose gets the strong model."""
    import inspect

    assert inspect.signature(client.complete_json).parameters["tier"].default == "small"
    assert inspect.signature(client.complete_text).parameters["tier"].default == "strong"


# ---------------------------------------------------------------------------
# Gemini request config
# ---------------------------------------------------------------------------
def test_gemini_3_drops_temperature_and_sets_thinking_level():
    cfg = client._gemini_config("sys", "gemini-3.8-flash", "strong", 0.7)
    assert cfg.temperature is None
    assert cfg.thinking_config.thinking_level.value == "HIGH"
    small = client._gemini_config("sys", "gemini-3.1-flash-lite", "small", 0.1)
    assert small.thinking_config.thinking_level.value == "LOW"


def test_older_gemini_keeps_temperature_and_has_no_thinking_level():
    cfg = client._gemini_config("sys", "gemini-2.5-flash", "small", 0.1)
    assert cfg.temperature == 0.1
    assert cfg.thinking_config is None


def test_thinking_level_env_override_and_bad_value(monkeypatch):
    monkeypatch.setenv("GEMINI_THINKING_SMALL", "minimal")
    assert client._thinking_level("small") == "minimal"
    monkeypatch.setenv("GEMINI_THINKING_SMALL", "bogus")
    assert client._thinking_level("small") == "low"


# ---------------------------------------------------------------------------
# Cost + usage extraction
# ---------------------------------------------------------------------------
def test_cost_bills_thinking_as_output_and_discounts_cached():
    # flash-lite: $0.25 in / $1.50 out / $0.025 cached per 1M.
    u = Usage(input_tokens=1_000_000, output_tokens=100_000, thinking_tokens=100_000, cached_tokens=400_000)
    expected = 600_000 * 0.25 / 1e6 + 400_000 * 0.025 / 1e6 + 200_000 * 1.50 / 1e6
    assert client.estimate_cost_usd("gemini-3.1-flash-lite", u) == pytest.approx(expected)


def test_unknown_model_costs_zero_without_raising():
    assert client.estimate_cost_usd("not-a-model", Usage(input_tokens=5, output_tokens=5)) == 0.0


def test_every_default_model_has_a_price():
    for tier in client.TIERS:
        assert client.model_for(tier, "gemini") in client.PRICES


def test_usage_from_gemini_metadata():
    resp = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=20,
            thoughts_token_count=30, cached_content_token_count=None,
        )
    )
    assert client._usage_from_gemini(resp) == Usage(100, 20, 30, 0)
    assert client._usage_from_gemini(SimpleNamespace()) == Usage()


def test_usage_from_openai_splits_reasoning_out_of_completion():
    resp = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=50, completion_tokens=40,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=25),
            prompt_tokens_details=SimpleNamespace(cached_tokens=10),
        )
    )
    assert client._usage_from_openai(resp) == Usage(50, 15, 25, 10)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
class _Ping(BaseModel):
    answer: str


def _fake_response(text="", function_calls=None, **meta):
    md = dict(prompt_token_count=100, candidates_token_count=10, thoughts_token_count=40,
              cached_content_token_count=0)
    md.update(meta)
    return SimpleNamespace(
        text=text, function_calls=function_calls, usage_metadata=SimpleNamespace(**md)
    )


@pytest.fixture()
def gemini(monkeypatch):
    """Active provider = gemini, with the network call replaced. Returns the call log."""
    monkeypatch.setattr(client, "LLM_PROVIDER", "gemini")
    calls: list[dict] = []
    response = {"value": _fake_response('{"answer": "OK"}')}

    def fake_generate(model, contents, config):
        calls.append({"model": model, "contents": contents, "config": config})
        return response["value"]

    monkeypatch.setattr(client, "_generate_gemini", fake_generate)
    calls_ns = SimpleNamespace(calls=calls, response=response)
    return calls_ns


def test_complete_json_logs_a_usage_row(db, gemini):
    out = client.complete_json("sys", "user", schema=_Ping, task="extract", job_id=7)
    assert out == {"answer": "OK"}
    assert gemini.calls[0]["model"] == client.GEMINI_MODEL_SMALL

    row = db.scalars(select(LlmUsage)).one()
    assert (row.task, row.tier, row.model, row.job_id) == (
        "extract", "small", client.GEMINI_MODEL_SMALL, 7,
    )
    assert (row.input_tokens, row.output_tokens, row.thinking_tokens) == (100, 10, 40)
    assert float(row.cost_usd) == pytest.approx(
        client.estimate_cost_usd(client.GEMINI_MODEL_SMALL, Usage(100, 10, 40, 0)), abs=1e-6
    )


def test_complete_text_defaults_to_strong_model(db, gemini):
    gemini.response["value"] = _fake_response("Dear Hiring Manager")
    assert client.complete_text("sys", "user", task="cover_letter", match_id=3) == "Dear Hiring Manager"
    assert gemini.calls[0]["model"] == client.GEMINI_MODEL_STRONG
    row = db.scalars(select(LlmUsage)).one()
    assert (row.tier, row.match_id) == ("strong", 3)


def test_failed_call_logs_nothing(db, gemini, monkeypatch):
    def boom(model, contents, config):
        raise LLMError("provider down")

    monkeypatch.setattr(client, "_generate_gemini", boom)
    with pytest.raises(LLMError):
        client.complete_json("sys", "user", schema=_Ping)
    assert db.scalars(select(LlmUsage)).all() == []


def test_logging_failure_never_fails_the_call(gemini, monkeypatch):
    def broken_session():
        raise RuntimeError("no such table: llm_usage")

    monkeypatch.setattr(client, "_usage_session", broken_session)
    # Budget check is skipped for small; logging error is swallowed.
    assert client.complete_json("sys", "user", schema=_Ping) == {"answer": "OK"}


# ---------------------------------------------------------------------------
# Budget guard
# ---------------------------------------------------------------------------
def test_under_budget_allows_mid_and_strong(db, gemini):
    _spend(db, 0.10)
    client.complete_text("sys", "user", tier="mid")
    client.complete_text("sys", "user", tier="strong")
    assert len(gemini.calls) == 2


def test_daily_cap_blocks_mid_and_strong_before_any_request(db, gemini):
    _set_prefs(db, llm_daily_budget_usd=1.0)
    _spend(db, 1.5)
    for tier in ("mid", "strong"):
        with pytest.raises(BudgetExceededError, match="Daily"):
            client.complete_text("sys", "user", tier=tier)
    assert gemini.calls == []  # nothing was sent, so nothing was spent


def test_small_is_never_blocked(db, gemini):
    _set_prefs(db, llm_daily_budget_usd=1.0, llm_total_budget_usd=2.0)
    _spend(db, 50.0)
    assert client.complete_json("sys", "user", schema=_Ping) == {"answer": "OK"}


def test_total_cap_blocks_even_when_today_is_clean(db, gemini):
    _set_prefs(db, llm_total_budget_usd=10.0)
    _spend(db, 11.0, when=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3))
    with pytest.raises(BudgetExceededError, match="Total"):
        client.complete_text("sys", "user", tier="strong")


def test_yesterdays_spend_does_not_count_against_daily_cap(db):
    _set_prefs(db, llm_daily_budget_usd=1.0)
    _spend(db, 5.0, when=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2))
    status = usage.budget_status(db)
    assert status["spent_today_usd"] == 0
    assert status["spent_total_usd"] == 5.0
    assert not status["blocked"]


@pytest.mark.parametrize("bad", [0, -5, "lots", None, True])
def test_bad_stored_cap_falls_back_to_default_not_unlimited(db, bad):
    _set_prefs(db, llm_daily_budget_usd=bad)
    _spend(db, 6.0)  # above the $5 default
    assert usage.budget_status(db)["blocked"]


def test_guard_fails_open_when_usage_table_unreadable(gemini, monkeypatch):
    def broken_session():
        raise RuntimeError("no such table")

    monkeypatch.setattr(client, "_usage_session", broken_session)
    assert client.complete_text("sys", "user", tier="strong") == '{"answer": "OK"}'


# ---------------------------------------------------------------------------
# complete_tools
# ---------------------------------------------------------------------------
_ECHO = ToolSpec(
    name="echo",
    description="Echo a word.",
    parameters={"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]},
)


def test_complete_tools_returns_the_tool_call(db, gemini):
    gemini.response["value"] = _fake_response(
        function_calls=[SimpleNamespace(name="echo", args={"word": "ping"})]
    )
    step = client.complete_tools(
        "sys", [{"role": "user", "content": "go"}], [_ECHO], run_id=9, task="agent_step"
    )
    assert (step.tool, step.args, step.text) == ("echo", {"word": "ping"}, None)

    sent = gemini.calls[0]
    assert sent["model"] == client.GEMINI_MODEL_MID
    cfg = sent["config"]
    assert cfg.tools[0].function_declarations[0].name == "echo"
    assert cfg.tool_config.function_calling_config.mode.value == "ANY"
    assert cfg.automatic_function_calling.disable is True
    row = db.scalars(select(LlmUsage)).one()
    assert (row.task, row.tier, row.run_id) == ("agent_step", "mid", 9)


def test_complete_tools_maps_assistant_role_to_model(gemini):
    gemini.response["value"] = _fake_response(function_calls=[SimpleNamespace(name="echo", args=None)])
    step = client.complete_tools(
        "sys",
        [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
        [_ECHO],
    )
    assert step.args == {}
    assert [c.role for c in gemini.calls[0]["contents"]] == ["user", "model"]


def test_complete_tools_text_answer_and_auto_mode(gemini):
    gemini.response["value"] = _fake_response(text="All done.")
    step = client.complete_tools(
        "sys", [{"role": "user", "content": "go"}], [_ECHO], require_tool=False
    )
    assert (step.tool, step.text) == (None, "All done.")
    assert gemini.calls[0]["config"].tool_config.function_calling_config.mode.value == "AUTO"


def test_complete_tools_rejects_empty_inputs(gemini):
    with pytest.raises(LLMError, match="at least one tool"):
        client.complete_tools("sys", [{"role": "user", "content": "x"}], [])
    with pytest.raises(LLMError, match="at least one message"):
        client.complete_tools("sys", [], [_ECHO])


def test_complete_tools_unsupported_on_groq(monkeypatch):
    monkeypatch.setattr(client, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(client, "_check_budget", lambda tier: None)
    with pytest.raises(LLMError, match="not supported"):
        client.complete_tools("sys", [{"role": "user", "content": "x"}], [_ECHO])


def test_complete_tools_openai_path(db, monkeypatch):
    monkeypatch.setattr(client, "LLM_PROVIDER", "openai")
    seen = {}

    class _Completions:
        def create(self, **kw):
            seen.update(kw)
            fn = SimpleNamespace(name="echo", arguments='{"word": "ping"}')
            msg = SimpleNamespace(tool_calls=[SimpleNamespace(function=fn)], content=None)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=msg)],
                usage=SimpleNamespace(prompt_tokens=20, completion_tokens=5),
            )

    fake = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    monkeypatch.setattr(client, "_get_openai_client", lambda: fake)
    monkeypatch.setattr(client, "_throttle", lambda: None)

    step = client.complete_tools("sys", [{"role": "user", "content": "go"}], [_ECHO])
    assert (step.tool, step.args) == ("echo", {"word": "ping"})
    assert seen["tool_choice"] == "required"
    assert seen["messages"][0] == {"role": "system", "content": "sys"}
    assert seen["tools"][0]["function"]["name"] == "echo"
    assert db.scalars(select(LlmUsage)).one().model == client.OPENAI_MODEL_LETTER


# ---------------------------------------------------------------------------
# GET /llm/usage
# ---------------------------------------------------------------------------
def test_usage_endpoint_reports_status(Session, db):
    _set_prefs(db, llm_daily_budget_usd=2.0)
    _spend(db, 2.5)

    def _override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    try:
        body = TestClient(app).get("/llm/usage").json()
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert body["blocked"] is True
    assert body["daily_cap_usd"] == 2.0
    assert body["spent_today_usd"] == 2.5
    assert "Daily" in body["reason"]
