"""/api/suggest: JSON validation, behaviour per case, and the fallback."""
import json

import httpx
import pytest

from app import llm
from tests.conftest import chat_reply

LUNCH = "Aarav, khana khaoge? Dal chawal hai, ya khichdi bana doon?"


def label(client, tile_id, lang):
    """Labels come from data/symbols.csv, so tests keep passing when the CSV is edited."""
    tiles = client.get(f"/api/symbols?lang={lang}").json()["tiles"]
    return next(t["label"] for t in tiles if t["id"] == tile_id)


# ---------- validate(): the server-side contract ----------

def test_partner_line_returns_only_quick_replies():
    out = llm.validate(
        {"quick_replies": ["dal_chawal", "khichdi", "wait", "something_else", "khichdi", "not_hungry"],
         "sentences": ["should be dropped"], "clarify": {"question": "q?", "options": ["a", "b"]}},
        has_input=False, clarified=False)
    assert out == {"quick_replies": ["dal_chawal", "khichdi", "not_hungry"], "sentences": [], "clarify": None}


def test_quick_replies_capped_and_cleaned():
    out = llm.validate({"quick_replies": ["a", "b", "c", "d", "e", "f", "g", " ", 5, "x" * 41],
                        "sentences": [], "clarify": None}, has_input=False, clarified=False)
    assert out["quick_replies"] == ["a", "b", "c", "d", "e"]


def test_sentences_capped_at_three_and_deduped():
    out = llm.validate({"quick_replies": ["yes"], "sentences": ["One.", "one.", "Two.", "Three.", "Four."],
                        "clarify": None}, has_input=True, clarified=False)
    assert out == {"quick_replies": [], "sentences": ["One.", "Two.", "Three."], "clarify": None}


def test_clarify_means_no_sentences():
    out = llm.validate({"quick_replies": [], "sentences": ["Mujhe kal pain hai."],
                        "clarify": {"question": "Pain now, or about tomorrow?", "options": ["Now", "Tomorrow"]}},
                       has_input=True, clarified=False)
    assert out["sentences"] == []
    assert out["clarify"] == {"question": "Pain now, or about tomorrow?", "options": ["Now", "Tomorrow"]}


def test_clarify_needs_two_or_three_options():
    raw = {"quick_replies": [], "sentences": ["Fine."], "clarify": {"question": "Sure?", "options": ["Yes"]}}
    assert llm.validate(raw, has_input=True, clarified=False)["clarify"] is None
    raw["clarify"]["options"] = ["a", "b", "c", "d"]
    assert llm.validate(raw, has_input=True, clarified=False)["clarify"]["options"] == ["a", "b", "c"]


def test_never_asks_twice_after_a_clarification():
    out = llm.validate({"quick_replies": [], "sentences": ["Mujhe abhi dard hai."],
                        "clarify": {"question": "Again?", "options": ["a", "b"]}},
                       has_input=True, clarified=True)
    assert out == {"quick_replies": [], "sentences": ["Mujhe abhi dard hai."], "clarify": None}


@pytest.mark.parametrize("raw", [
    None, [], "text",
    {"quick_replies": [], "sentences": [], "clarify": None},                       # nothing usable
    {"quick_replies": [], "sentences": [], "clarify": {"question": "", "options": ["a", "b"]}},
])
def test_unusable_output_raises(raw):
    with pytest.raises(ValueError):
        llm.validate(raw, has_input=True, clarified=False)


def test_system_prompt_carries_the_core_rules():
    prompt = llm.build_system_prompt({"name": "Aarav", "gender": "male"}, "Hinglish", [{"id": "tea", "label_en": "Tea"}])
    for rule in ("Never add facts, feelings or requests the user did not select.",
                 "Prefer short sentences.", "Ask when unsure."):
        assert rule in prompt
    assert "tea: Tea" in prompt


# ---------- /api/suggest endpoint ----------

def test_sentences_pass_through(client, chat):
    chat.result = chat_reply({"quick_replies": [], "sentences": ["Khichdi chahiye, thodi soft."], "clarify": None})
    r = client.post("/api/suggest", json={"history": [LUNCH], "tiles": ["khichdi", "soft_food"], "lang": "hinglish"})
    assert r.status_code == 200
    body = r.json()
    assert body["sentences"] == ["Khichdi chahiye, thodi soft."]
    assert body["fallback"] is False


def test_request_contents_sent_to_llm(client, chat):
    history = [f"line {i}" for i in range(10)]
    client.post("/api/suggest", json={"history": history, "tiles": ["pain", "nonsense", "pain", "tomorrow"],
                                      "typed": "  abhi  ", "lang": "hi"})
    (_url, body, key), _ = chat.calls[0]
    user = json.loads(body["messages"][1]["content"])
    assert user["partner_lines_oldest_first"] == history[-6:]                  # last 6 only
    assert [t["id"] for t in user["selected_tiles"]] == ["pain", "tomorrow"]   # unknown + duplicate dropped
    assert user["selected_tiles"][0]["label"] == label(client, "pain", "hi")   # label in the user's language
    assert user["typed_text"] == "abhi"
    assert body["response_format"]["type"] == "json_schema"
    assert key == "test-openai-key"


@pytest.mark.parametrize("failure", [
    httpx.ConnectError("down"),                                  # network
    httpx.ReadTimeout("slow"),                                   # timeout
    chat_reply("not json at all"),                               # malformed JSON
    chat_reply({"sentences": "wrong type"}),                     # wrong shape
    (429, {"error": {"message": "rate limited"}}),               # HTTP error
    chat_reply({"quick_replies": [], "sentences": [], "clarify": None}),  # empty
])
def test_any_llm_failure_falls_back_to_tile_labels(client, chat, failure):
    chat.result = failure
    r = client.post("/api/suggest", json={"tiles": ["khichdi", "soft_food"], "typed": "jaldi", "lang": "hinglish"})
    assert r.status_code == 200
    body = r.json()
    assert body["fallback"] is True
    assert body["sentences"] == [f"{label(client, 'khichdi', 'hinglish')}, {label(client, 'soft_food', 'hinglish')}, jaldi"]
    assert body["clarify"] is None


def test_fallback_uses_the_active_language(client, chat):
    chat.result = httpx.ConnectError("down")
    body = client.post("/api/suggest", json={"tiles": ["khichdi", "soft_food"], "lang": "hi"}).json()
    assert body["sentences"] == [f"{label(client, 'khichdi', 'hi')}, {label(client, 'soft_food', 'hi')}"]


def test_missing_openai_key_falls_back(client, chat, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    body = client.post("/api/suggest", json={"tiles": ["water"], "lang": "en"}).json()
    assert body["fallback"] is True
    assert body["sentences"] == [label(client, "water", "en")]
    assert chat.calls == []


def test_partner_line_fallback_offers_basic_replies(client, chat):
    chat.result = httpx.ConnectError("down")
    body = client.post("/api/suggest", json={"history": [LUNCH], "lang": "hinglish"}).json()
    assert body["fallback"] is True
    assert body["quick_replies"] == ["yes", "no", "dont_know", "repeat"]
    assert body["sentences"] == []


def test_nothing_to_suggest_makes_no_llm_call(client, chat):
    body = client.post("/api/suggest", json={"lang": "en"}).json()
    assert body["quick_replies"] == [] and body["sentences"] == []
    assert chat.calls == []


def test_bad_language_rejected(client, chat):
    assert client.post("/api/suggest", json={"tiles": ["water"], "lang": "xx"}).status_code == 400
