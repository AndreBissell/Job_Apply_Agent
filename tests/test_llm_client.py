"""Tests for the Gemini client construction in app/llm/client.py.

No network: google.genai.Client is replaced with a recorder, and the module's
env-derived globals are monkeypatched per test (they're read once at import).
"""
from __future__ import annotations

import pytest
from google import genai

from app.llm import client
from app.llm.client import LLMError


@pytest.fixture()
def fake_genai(monkeypatch):
    """Record the kwargs genai.Client is built with; reset the cached client."""
    calls: list[dict] = []

    class _FakeClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(genai, "Client", _FakeClient)
    monkeypatch.setattr(client, "_gemini_client", None)
    monkeypatch.setattr(client, "_inject_truststore", lambda: None)
    return calls


def test_vertex_mode_uses_adc_project_and_location(fake_genai, monkeypatch):
    monkeypatch.setattr(client, "GEMINI_USE_VERTEX", True)
    monkeypatch.setattr(client, "GOOGLE_CLOUD_PROJECT", "seek-job-application")
    monkeypatch.setattr(client, "GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.setattr(client, "GEMINI_API_KEY", "should-be-ignored")

    client._get_gemini_client()

    assert fake_genai == [
        {"vertexai": True, "project": "seek-job-application", "location": "global"}
    ]


def test_vertex_mode_without_project_raises(fake_genai, monkeypatch):
    monkeypatch.setattr(client, "GEMINI_USE_VERTEX", True)
    monkeypatch.setattr(client, "GOOGLE_CLOUD_PROJECT", None)

    with pytest.raises(LLMError, match="GOOGLE_CLOUD_PROJECT"):
        client._get_gemini_client()
    assert fake_genai == []


def test_api_key_mode_unchanged(fake_genai, monkeypatch):
    monkeypatch.setattr(client, "GEMINI_USE_VERTEX", False)
    monkeypatch.setattr(client, "GEMINI_API_KEY", "test-key")

    client._get_gemini_client()

    assert fake_genai == [{"api_key": "test-key"}]


def test_api_key_mode_without_key_raises(fake_genai, monkeypatch):
    monkeypatch.setattr(client, "GEMINI_USE_VERTEX", False)
    monkeypatch.setattr(client, "GEMINI_API_KEY", None)

    with pytest.raises(LLMError, match="No API key"):
        client._get_gemini_client()


def test_client_is_cached(fake_genai, monkeypatch):
    monkeypatch.setattr(client, "GEMINI_USE_VERTEX", False)
    monkeypatch.setattr(client, "GEMINI_API_KEY", "test-key")

    first = client._get_gemini_client()
    assert client._get_gemini_client() is first
    assert len(fake_genai) == 1
