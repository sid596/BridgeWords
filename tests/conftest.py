"""Shared fixtures. Every network call is mocked: tests never reach Gnani or OpenAI."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import gnani, llm


class Recorder:
    """Records calls and returns a scripted result (or raises it, if it's an exception)."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result(*args, **kwargs) if callable(self.result) else self.result


def chat_reply(content: dict | str, status: int = 200):
    """A fake OpenAI chat completion."""
    text = content if isinstance(content, str) else json.dumps(content)
    return status, {"choices": [{"message": {"content": text}}]}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """Block real network, use dummy keys and a temporary TTS cache."""

    def no_network(*a, **k):
        raise AssertionError("A test tried to make a real network call")

    monkeypatch.setattr(httpx.Client, "send", no_network)
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    monkeypatch.setenv("GNANI_API_KEY", "test-gnani-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setattr(main, "TTS_CACHE_DIR", tmp_path / "tts")
    main._tts_memory.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def tts(monkeypatch):
    rec = Recorder(lambda text, **kw: b"RIFF" + text.encode("utf-8"))
    monkeypatch.setattr(gnani, "synthesize", rec)
    return rec


@pytest.fixture
def stt(monkeypatch):
    rec = Recorder({"success": True, "transcript": " Dal chawal  ya khichdi? ", "detected_language_code": "hi-IN"})
    monkeypatch.setattr(gnani, "transcribe", rec)
    return rec


@pytest.fixture
def chat(monkeypatch):
    """Fake OpenAI. Set chat.result to chat_reply(...) or an exception."""
    rec = Recorder(chat_reply({"quick_replies": [], "sentences": ["ok"], "clarify": None}))

    async def fake_post(url, body, key):
        return rec(url, body, key)

    monkeypatch.setattr(llm, "post_chat", fake_post)
    return rec
