"""Personal board learning: the AI proposes, the user decides, evidence is checked."""
import json

import httpx
import pytest

from app import learning
from tests.conftest import chat_reply

PARACHUTE = {
    "id": "parachute", "emoji": "🪂", "label_en": "Parachute", "label_hinglish": "Parachute",
    "label_hi": "पैराशूट", "label_kn": "ಪ್ಯಾರಾಶೂಟ್", "label_mr": "पॅराशूट",
    "reason": "Parachutes came up in 2 conversations.",
}


def proposals(*items):
    return chat_reply({"proposals": list(items)})


def log_lines(client, lines, kind="partner"):
    for line in lines:
        learning.add_event(kind, line)


def tile_ids(client, lang="en"):
    return {t["id"] for t in client.get(f"/api/symbols?lang={lang}").json()["tiles"]}


SKYDIVING = ["Parachute pack kar liya?", "Sunday ko skydiving hai, parachute check karna.",
             "Chai piyoge?", "Aaj weather accha hai."]


# ---------- The core loop ----------

def test_ai_proposes_but_never_adds_a_tile_by_itself(client, chat):
    log_lines(client, SKYDIVING)
    chat.result = proposals({**PARACHUTE, "evidence": SKYDIVING[:2]})
    pending = client.post("/api/learn/propose?lang=hinglish").json()["pending"]
    assert [p["id"] for p in pending] == ["parachute"]
    assert pending[0]["label"] == "Parachute" and pending[0]["emoji"] == "🪂"
    assert "parachute" not in tile_ids(client)          # not on the board until the user says so


def test_accepting_adds_it_to_my_tiles_in_every_language(client, chat):
    log_lines(client, SKYDIVING)
    chat.result = proposals({**PARACHUTE, "evidence": SKYDIVING[:2]})
    client.post("/api/learn/propose")
    assert client.post("/api/learn/accept", json={"id": "parachute"}).status_code == 200

    data = client.get("/api/symbols?lang=hi").json()
    assert data["categories"][0] == "My tiles"           # the user's own tiles come first
    tile = next(t for t in data["tiles"] if t["id"] == "parachute")
    assert tile["label"] == "पैराशूट" and tile["learned"] is True
    assert client.get("/api/learn").json()["pending"] == []


def test_learned_tiles_can_be_used_in_suggestions(client, chat):
    log_lines(client, SKYDIVING)
    chat.result = proposals({**PARACHUTE, "evidence": SKYDIVING[:2]})
    client.post("/api/learn/propose")
    client.post("/api/learn/accept", json={"id": "parachute"})
    chat.result = httpx.ConnectError("down")  # fallback path shows the tile was recognised
    body = client.post("/api/suggest", json={"tiles": ["parachute", "want"], "lang": "en"}).json()
    assert body["sentences"][0].startswith("Parachute")


def test_rejected_ideas_are_never_proposed_again(client, chat):
    log_lines(client, SKYDIVING)
    chat.result = proposals({**PARACHUTE, "evidence": SKYDIVING[:2]})
    client.post("/api/learn/propose")
    client.post("/api/learn/reject", json={"id": "parachute"})
    log_lines(client, ["Parachute naya aaya", "Parachute bada hai", "x y z", "a b c"])
    pending = client.post("/api/learn/propose").json()["pending"]
    assert pending == []
    system_prompt = chat.calls[-1][0][1]["messages"][0]["content"]
    assert "parachute" in system_prompt.split("REJECTED BEFORE")[1]


# ---------- Evidence checks (no invented interests) ----------

def test_proposal_without_real_evidence_is_dropped(client, chat):
    log_lines(client, ["Chai piyoge?", "Khana ready hai.", "TV band karo?", "Kal school hai."])
    chat.result = proposals({**PARACHUTE, "evidence": ["He loves skydiving", "Parachute again"]})
    assert client.post("/api/learn/propose").json()["pending"] == []


def test_one_mention_is_not_enough_unless_the_user_typed_it(client, chat):
    log_lines(client, ["Crutch kahan rakhi?", "Chai?", "Khana?", "Paani?"])
    crutch = {**PARACHUTE, "id": "crutch", "emoji": "🩼", "label_en": "Crutch", "evidence": ["Crutch kahan rakhi?"]}
    chat.result = proposals(crutch)
    assert client.post("/api/learn/propose").json()["pending"] == []

    learning.add_event("typed", "crutch")
    for line in ["a1 b1", "a2 b2", "a3 b3"]:
        learning.add_event("partner", line)
    chat.result = proposals({**crutch, "evidence": ["crutch"]})
    assert [p["id"] for p in client.post("/api/learn/propose").json()["pending"]] == ["crutch"]


@pytest.mark.parametrize("bad", [
    {"id": "khichdi"},                     # already on the board
    {"label_en": "Khichdi"},               # same label as an existing tile
    {"id": "Bad Id!"},                     # not a clean id
    {"emoji": "P"},                        # not an emoji
    {"label_kn": ""},                      # missing a language
    {"label_hi": "x" * 31},                # too long for a tile
])
def test_malformed_or_duplicate_proposals_are_dropped(client, chat, bad):
    log_lines(client, SKYDIVING)
    chat.result = proposals({**PARACHUTE, "evidence": SKYDIVING[:2], **bad})
    assert client.post("/api/learn/propose").json()["pending"] == []


def test_waits_for_enough_new_conversation_before_asking_the_ai(client, chat):
    log_lines(client, SKYDIVING[:2])
    client.post("/api/learn/propose")
    assert chat.calls == []                              # only 2 new lines: no AI call
    log_lines(client, SKYDIVING[2:])
    chat.result = proposals()
    client.post("/api/learn/propose")
    assert len(chat.calls) == 1
    client.post("/api/learn/propose")
    assert len(chat.calls) == 1                          # nothing new since the last analysis


def test_learning_failure_never_breaks_the_app(client, chat):
    log_lines(client, SKYDIVING)
    chat.result = httpx.ConnectError("down")
    r = client.post("/api/learn/propose")
    assert r.status_code == 200 and r.json()["pending"] == []


# ---------- Where the log comes from ----------

def test_partner_lines_typed_text_and_spoken_sentences_are_logged(client, chat, stt):
    client.post("/api/listen", files={"audio": ("p.wav", b"RIFF" + b"0" * 100, "audio/wav")})
    client.post("/api/suggest", json={"tiles": ["want"], "typed": "parachute", "lang": "en"})
    client.post("/api/learn/spoke", json={"text": "I want my parachute."})
    client.post("/api/learn/spoke", json={"text": "I want my parachute."})   # repeat: not new evidence
    events = learning.load()["events"]
    assert [(e["kind"], e["text"]) for e in events] == [
        ("partner", "Dal chawal ya khichdi?"), ("typed", "parachute"), ("spoke", "I want my parachute.")]


def test_learning_can_be_switched_off_in_the_profile(client, chat, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "load_profile", lambda: {"name": "Test", "learning": False})
    client.post("/api/learn/spoke", json={"text": "hello there"})
    assert learning.load()["events"] == []


def test_forget_wipes_the_log_but_keeps_accepted_tiles(client, chat):
    log_lines(client, SKYDIVING)
    chat.result = proposals({**PARACHUTE, "evidence": SKYDIVING[:2]})
    client.post("/api/learn/propose")
    client.post("/api/learn/accept", json={"id": "parachute"})
    client.post("/api/learn/forget")
    assert learning.load()["events"] == []
    assert "parachute" in tile_ids(client)


# ---------- Hiding tiles (pasta instead of khichdi) ----------

def test_hide_and_restore_starter_tiles(client):
    client.post("/api/tiles/hide", json={"id": "khichdi"})
    data = client.get("/api/symbols").json()
    assert "khichdi" not in {t["id"] for t in data["tiles"]}
    assert data["hidden_count"] == 1
    client.post("/api/tiles/unhide")
    assert "khichdi" in tile_ids(client)


def test_urgent_tiles_can_never_be_hidden(client):
    assert client.post("/api/tiles/hide", json={"id": "help"}).status_code == 400
    assert "help" in tile_ids(client)


def test_removing_a_learned_tile_also_stops_it_coming_back(client, chat):
    log_lines(client, SKYDIVING)
    chat.result = proposals({**PARACHUTE, "evidence": SKYDIVING[:2]})
    client.post("/api/learn/propose")
    client.post("/api/learn/accept", json={"id": "parachute"})
    client.post("/api/tiles/hide", json={"id": "parachute"})
    assert "parachute" not in tile_ids(client)
    assert "parachute" in learning.load()["rejected"]


def test_hinglish_board_uses_hinglish_labels(client):
    tiles = {t["id"]: t["label"] for t in client.get("/api/symbols?lang=hinglish").json()["tiles"]}
    import csv
    with open("data/symbols.csv", encoding="utf-8", newline="") as f:
        row = next(r for r in csv.DictReader(f) if r["id"] == "dal_chawal")
    assert tiles["dal_chawal"] == row["label_hinglish"] != row["label_en"]
