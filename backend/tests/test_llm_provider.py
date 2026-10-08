"""Provider-layer tests: local OpenAI-compatible backend, JSON extraction,
auto-fallback, and config validation. Uses httpx.MockTransport (no real server).
Run with the repo venv: .venv/bin/python -m pytest backend/tests/test_llm_provider.py -q
"""

import asyncio
import json
import os
from typing import List

import httpx
import pytest

from backend.llm_provider import (
    LocalOpenAIProvider,
    GeminiProvider,
    ProviderError,
    resolve_provider,
)
from backend.gemini_service import extract_json_metadata


def _mock_local_client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="http://mock.local/v1", transport=transport)
    provider = LocalOpenAIProvider(base_url="http://mock.local/v1",
                                   model="tiny-1b")
    provider._client = client
    return provider


def _chat_completion(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


# ---------------------------------------------------------------------------
# Local provider: dialogue generation
# ---------------------------------------------------------------------------

def test_local_provider_generates_dialogue():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_completion("The guard nods slowly."))

    provider = _mock_local_client(handler)
    history = [
        {"role": "user", "parts": ["Hello guard."]},
        {"role": "model", "parts": ["State your business."]},
        {"role": "user", "parts": ["I seek the blacksmith."]},
    ]
    text = asyncio.run(provider.generate(history, system_instruction="Be terse."))

    assert text == "The guard nods slowly."
    assert captured["url"].endswith("/chat/completions")
    msgs = captured["payload"]["messages"]
    assert msgs[0] == {"role": "system", "content": "Be terse."}
    assert msgs[1]["role"] == "user" and "Hello guard." in msgs[1]["content"]
    assert msgs[2]["role"] == "model"
    assert captured["payload"]["model"] == "tiny-1b"
    assert "response_format" not in captured["payload"]


def test_local_provider_json_mode_and_retry():
    payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if "response_format" in payload:
            # Simulate a server that does not support response_format.
            return httpx.Response(400, json={"error": "response_format unsupported"})
        return httpx.Response(200, json=_chat_completion('{"dialogue": "Hi."}'))

    provider = _mock_local_client(handler)
    text = asyncio.run(provider.generate(
        [{"role": "user", "parts": ["hi"]}], json_mode=True))

    assert text == '{"dialogue": "Hi."}'
    assert "response_format" in payloads[0]     # tried json_object first
    assert "response_format" not in payloads[1]  # fell back cleanly


def test_local_provider_json_mode_accepted():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json=_chat_completion('{"a": 1}'))

    provider = _mock_local_client(handler)
    text = asyncio.run(provider.generate(
        [{"role": "user", "parts": ["x"]}], json_mode=True))
    assert text == '{"a": 1}'


def test_local_provider_list_models():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{"id": "tiny-1b"}, {"id": "mini-3b"}]})

    provider = _mock_local_client(handler)
    models = asyncio.run(provider.list_models())
    assert models == ["tiny-1b", "mini-3b"]


def test_local_provider_reachability():
    def ok(request): return httpx.Response(200, json={"data": [{"id": "m"}]})

    def down(request): return httpx.Response(500)

    assert asyncio.run(_mock_local_client(ok).is_reachable()) is True
    assert asyncio.run(_mock_local_client(down).is_reachable()) is False


# ---------------------------------------------------------------------------
# Metadata extraction (provider-agnostic, messy local-model output)
# ---------------------------------------------------------------------------

def test_extract_json_metadata_fenced_json_block():
    text = 'Brom wipes his brow.\n```json\n{"dialogue": "Ah, traveler!", "mood": "friendly"}\n```'
    dialogue, meta = extract_json_metadata(text)
    assert dialogue == "Ah, traveler!"
    assert meta["mood"] == "friendly"


def test_extract_json_metadata_raw_json_with_prose():
    # Small local models often emit prose + bare JSON.
    text = 'Sure! Here is my response:\n{"dialogue": "Welcome.", "quest_offered": {"id": "fetch_ore"}}'
    dialogue, meta = extract_json_metadata(text)
    assert dialogue == "Welcome."
    assert meta["quest_offered"]["id"] == "fetch_ore"


def test_extract_json_metadata_generic_fence():
    text = '```\n{"updated_memory": ["a"], "new_greetings": ["g1"]}\n```'
    dialogue, meta = extract_json_metadata(text)
    assert meta["updated_memory"] == ["a"]
    assert meta["new_greetings"] == ["g1"]


def test_extract_json_metadata_malformed_json_is_dialogue():
    text = 'I say hello! {broken json here'
    dialogue, meta = extract_json_metadata(text)
    assert dialogue == text
    assert meta == {}


def test_local_full_turn_dialogue_plus_metadata():
    """End-to-end through the local provider: dialogue + messy JSON extraction."""
    raw = ('The blacksmith looks up.\n```json\n'
           '{"dialogue": "Need a blade sharpened?", "skill_check": '
           '{"type": "charisma", "dc": 10, "difficulty": "easy"}}\n```')

    def handler(request):
        return httpx.Response(200, json=_chat_completion(raw))

    provider = _mock_local_client(handler)
    text = asyncio.run(provider.generate([{"role": "user", "parts": ["hi"]}]))
    dialogue, meta = extract_json_metadata(text)
    # Existing contract: a JSON "dialogue" field becomes the primary dialogue.
    assert dialogue == "Need a blade sharpened?"
    assert meta["skill_check"]["dc"] == 10


# ---------------------------------------------------------------------------
# Provider resolution / auto-fallback / config validation
# ---------------------------------------------------------------------------

class _FakeProvider:
    def __init__(self, name, reachable=True, configured=True):
        self.name = name
        self.base_url = "http://fake.local/v1"
        self._reachable = reachable
        self._configured = configured

    async def is_reachable(self):
        return self._reachable

    def is_configured(self):
        return self._configured


def test_auto_prefers_local_when_reachable():
    p = asyncio.run(resolve_provider(
        mode="auto",
        local=_FakeProvider("local", reachable=True),
        gemini=_FakeProvider("gemini", configured=True)))
    assert p.name == "local"


def test_auto_falls_back_to_gemini_when_local_down():
    p = asyncio.run(resolve_provider(
        mode="auto",
        local=_FakeProvider("local", reachable=False),
        gemini=_FakeProvider("gemini", configured=True)))
    assert p.name == "gemini"


def test_auto_raises_when_nothing_available():
    with pytest.raises(ProviderError):
        asyncio.run(resolve_provider(
            mode="auto",
            local=_FakeProvider("local", reachable=False),
            gemini=_FakeProvider("gemini", configured=False)))


def test_local_mode_requires_no_gemini_key(monkeypatch):
    """provider=local must not need GEMINI_API_KEY at resolve or import time."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("backend.llm_provider._provider", None)
    from backend import gemini_service  # import-time raise was the old bug
    p = asyncio.run(resolve_provider(
        mode="local",
        local=_FakeProvider("local", reachable=True),
        gemini=_FakeProvider("gemini", configured=False)))
    assert p.name == "local"


def test_gemini_provider_not_configured_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    g = GeminiProvider()
    assert g.is_configured() is False
    with pytest.raises(ProviderError):
        g._get_client()
