"""Urgent tiles, /api/speak caching and errors, /api/listen."""
import httpx
import pytest

from app import llm

LANGS = ["hinglish", "hi", "en", "kn", "mr"]


# ---------- Urgent tiles ----------

@pytest.mark.parametrize("lang", LANGS)
def test_urgent_tiles_carry_a_fixed_phrase(client, lang):
    tiles = {t["id"]: t for t in client.get(f"/api/symbols?lang={lang}").json()["tiles"]}
    urgent = sorted(t["id"] for t in tiles.values() if t["urgent"])
    assert urgent == ["breathing", "help"]
    for tid in urgent:
        assert tiles[tid]["say"].strip()
    assert tiles["wait"]["say"].strip()                 # Wait speaks a fixed phrase too
    assert "say" not in tiles["khichdi"]                # ordinary tiles never speak by themselves


def test_urgent_phrase_speaks_without_any_llm_step(client, tts, monkeypatch):
    async def llm_must_not_run(*a, **k):
        raise AssertionError("urgent speech must not call the LLM")

    monkeypatch.setattr(llm, "post_chat", llm_must_not_run)
    say = next(t["say"] for t in client.get("/api/symbols?lang=hinglish").json()["tiles"] if t["id"] == "help")
    r = client.post("/api/speak", json={"text": say, "lang": "hinglish"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert len(tts.calls) == 1


def test_urgent_audio_is_cached_after_first_fetch(client, tts):
    for _ in range(3):
        assert client.post("/api/speak", json={"text": "Help! Please come now.", "lang": "en"}).status_code == 200
    assert len(tts.calls) == 1


# ---------- /api/speak ----------

def test_speak_uses_profile_voice_and_hinglish_code(client, tts):
    r = client.post("/api/speak", json={"text": "Khichdi chahiye.", "lang": "hinglish"})
    assert r.headers["x-cache"] == "miss"
    _, kwargs = tts.calls[0]
    import app.main as main
    model, voice = main.tts_voice("hinglish")     # whatever data/profile.json selects
    assert kwargs == {"language": "hi-en", "voice": voice, "model": model}


def test_speak_cache_normalises_whitespace_and_survives_restart(client, tts):
    import app.main as main
    client.post("/api/speak", json={"text": "  Khichdi,   please. ", "lang": "hinglish"})
    assert client.post("/api/speak", json={"text": "Khichdi, please.", "lang": "hinglish"}).headers["x-cache"] == "hit"
    main._tts_memory.clear()  # simulate a server restart: the disk cache remains
    assert client.post("/api/speak", json={"text": "Khichdi, please.", "lang": "hinglish"}).headers["x-cache"] == "hit"
    assert len(tts.calls) == 1


def test_same_text_in_another_language_is_a_new_phrase(client, tts):
    client.post("/api/speak", json={"text": "OK", "lang": "hi"})
    client.post("/api/speak", json={"text": "OK", "lang": "kn"})
    assert [kw["language"] for _, kw in tts.calls] == ["hi-IN", "kn-IN"]


def test_speak_without_key_is_a_clear_503(client, tts, monkeypatch):
    monkeypatch.delenv("GNANI_API_KEY")
    r = client.post("/api/speak", json={"text": "Hello", "lang": "en"})
    assert r.status_code == 503
    assert "GNANI_API_KEY" in r.json()["detail"]
    assert tts.calls == []


@pytest.mark.parametrize("error, fragment", [
    (httpx.HTTPStatusError("x", request=httpx.Request("POST", "https://x"), response=httpx.Response(401)), "HTTP 401"),
    (httpx.ConnectError("down"), "Could not reach"),
    (ValueError("Timbre did not return the requested WAV audio"), "WAV"),
])
def test_timbre_errors_become_502(client, tts, error, fragment):
    tts.result = error
    r = client.post("/api/speak", json={"text": "Hello", "lang": "en"})
    assert r.status_code == 502
    assert fragment in r.json()["detail"]
    assert "test-gnani-key" not in r.text


@pytest.mark.parametrize("body, status", [
    ({"text": "   ", "lang": "en"}, 400),
    ({"text": "a" * 401, "lang": "en"}, 422),
    ({"text": "hi", "lang": "xx"}, 400),
])
def test_speak_input_validation(client, tts, body, status):
    assert client.post("/api/speak", json=body).status_code == status
    assert tts.calls == []


# ---------- /api/listen ----------

WAV = b"RIFF" + b"\x00" * 1000


def test_listen_returns_transcript_and_language(client, stt):
    r = client.post("/api/listen", files={"audio": ("partner.wav", WAV, "audio/wav")}, data={"lang": "hinglish"})
    assert r.status_code == 200
    body = r.json()
    assert body["transcript"] == "Dal chawal ya khichdi?"
    assert body["language"] == "hi-IN"
    _, kwargs = stt.calls[0]
    assert kwargs["language"] == "hi-IN,en-IN" and kwargs["fmt"] == "codemix"


def test_listen_kannada_uses_kannada_plus_english(client, stt):
    client.post("/api/listen", files={"audio": ("p.wav", WAV, "audio/wav")}, data={"lang": "kn"})
    assert stt.calls[0][1]["language"] == "kn-IN,en-IN"


def test_listen_rejects_empty_and_oversized_audio(client, stt):
    assert client.post("/api/listen", files={"audio": ("p.wav", b"", "audio/wav")}).status_code == 400
    big = b"0" * (5 * 1024 * 1024 + 1)
    assert client.post("/api/listen", files={"audio": ("p.wav", big, "audio/wav")}).status_code == 413
    assert stt.calls == []


def test_listen_prisma_error_is_502(client, stt):
    stt.result = httpx.ConnectError("down")
    r = client.post("/api/listen", files={"audio": ("p.wav", WAV, "audio/wav")})
    assert r.status_code == 502
