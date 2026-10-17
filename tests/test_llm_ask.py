"""
Tests for POST /llm/ask. vLLM is never contacted: the OpenAI client's
create() is monkeypatched to return fake stream chunks or raise.
"""

from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

import llm_service
from models import Asset, AssetType


def chunk(content):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


@pytest.fixture()
def fake_create(monkeypatch):
    """Replace client.chat.completions.create; returns the kwargs it was called with."""
    calls = []

    def install(result):
        def create(**kwargs):
            calls.append(kwargs)
            if isinstance(result, Exception):
                raise result
            return result
        monkeypatch.setattr(llm_service.client.chat.completions, "create", create)
        return calls

    return install


def connection_error():
    return APIConnectionError(request=httpx.Request("POST", "http://vllm.test/v1/chat/completions"))


def test_ask_streams_tokens(client, auth_headers, fake_create):
    fake_create(iter([chunk("Hello"), chunk(None), chunk(" grid")]))

    response = client.post("/llm/ask", params={"question": "Hi?"}, headers=auth_headers)

    assert response.status_code == 200
    assert response.text == "Hello grid"


def test_ask_injects_battery_data_into_system_prompt(client, db_session, auth_headers, fake_create):
    db_session.add(Asset(
        eic_code="10T-FR-BATT-01", name="Battery One", asset_type=AssetType.BATTERY,
        max_capacity_mwh=10.0, max_charge_rate_mw=2.0, max_discharge_rate_mw=2.0,
    ))
    db_session.commit()
    calls = fake_create(iter([]))

    client.post("/llm/ask", params={"question": "Which batteries?"}, headers=auth_headers)

    messages = calls[0]["messages"]
    assert "Battery One" in messages[0]["content"]
    assert "10.0 MWh" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "Which batteries?"}


def test_ask_returns_503_when_vllm_unreachable(client, auth_headers, fake_create):
    fake_create(connection_error())

    response = client.post("/llm/ask", params={"question": "Hi?"}, headers=auth_headers)

    assert response.status_code == 503


def test_ask_flags_stream_interrupted_midway(client, auth_headers, fake_create):
    def broken_stream():
        yield chunk("Partial")
        raise connection_error()

    fake_create(broken_stream())

    response = client.post("/llm/ask", params={"question": "Hi?"}, headers=auth_headers)

    assert response.status_code == 200
    assert response.text.startswith("Partial")
    assert "[Error: the LLM stream was interrupted]" in response.text


def test_ask_requires_api_key(client):
    response = client.post("/llm/ask", params={"question": "Hi?"})

    assert response.status_code in (401, 403)
