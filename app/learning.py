"""Personal board learning.

The board starts with tiles most people need. Over time BRIDGEWORDS notices what *this*
person's life is about (a hobby, a food from their culture, a crutch, a friend's name) and
proposes new tiles. Rules:

- The AI only PROPOSES. A tile is added only when the user taps "Add" (same principle as Speak).
- Every proposal must quote real lines from the conversation log; quotes are checked here.
- Tiles never move by themselves (AAC users rely on motor memory): new tiles go to "My tiles",
  and the user can hide tiles they never use.
- Everything is stored locally in user_data/ (git-ignored). "Forget" wipes the conversation log.
"""
import json
import logging
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("bridgewords.learning")

ROOT = Path(__file__).resolve().parent.parent
USER_DATA = ROOT / "user_data"

MY_TILES = "My tiles"
LABEL_COLS = ("label_en", "label_hinglish", "label_hi", "label_kn", "label_mr")
EVENT_KINDS = ("partner", "spoke", "typed")
MAX_EVENTS = 300          # conversation log kept on disk
EVENTS_FOR_ANALYSIS = 120  # most recent entries the AI looks at
MIN_NEW_EVENTS = 4        # don't re-analyse until this many new entries arrived
MAX_PENDING = 5
MAX_PROPOSALS = 3
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")

_lock = threading.Lock()


def _empty() -> dict:
    return {"tiles": [], "pending": [], "rejected": [], "hidden": [], "events": [],
            "events_total": 0, "analysed_at": 0}


def store_path() -> Path:
    return USER_DATA / "learned.json"


def load() -> dict:
    path = store_path()
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        log.warning("learned.json unreadable; starting fresh")
        return _empty()
    return {**_empty(), **data}


def save(store: dict) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def update(fn):
    """Load, modify with fn(store), save, under one lock. Returns fn's result."""
    with _lock:
        store = load()
        result = fn(store)
        save(store)
        return result


# ---------- Conversation log ----------

def add_event(kind: str, text: str) -> None:
    text = " ".join(text.split())[:300]
    if kind not in EVENT_KINDS or not text:
        return

    def fn(store):
        last = next((e for e in reversed(store["events"]) if e["kind"] == kind), None)
        if last and last["text"] == text:
            return  # same line again (e.g. Speak tapped twice): not new evidence
        store["events"].append({"t": int(time.time()), "kind": kind, "text": text})
        store["events"] = store["events"][-MAX_EVENTS:]
        store["events_total"] += 1

    update(fn)


def forget() -> None:
    """Wipe the conversation log and pending ideas. Accepted tiles stay."""
    def fn(store):
        store["events"] = []
        store["pending"] = []
        store["analysed_at"] = store["events_total"]

    update(fn)


# ---------- Tiles ----------

def learned_rows() -> list[dict]:
    """Accepted tiles, in the same shape as symbols.csv rows."""
    return [{**t, "category": MY_TILES} for t in load()["tiles"]]


def hidden_ids() -> set[str]:
    return set(load()["hidden"])


def accept(tile_id: str) -> dict | None:
    def fn(store):
        tile = next((p for p in store["pending"] if p["id"] == tile_id), None)
        if tile:
            store["pending"] = [p for p in store["pending"] if p["id"] != tile_id]
            store["tiles"].append({k: tile[k] for k in ("id", "emoji", *LABEL_COLS)})
        return tile

    return update(fn)


def reject(tile_id: str) -> None:
    def fn(store):
        store["pending"] = [p for p in store["pending"] if p["id"] != tile_id]
        if tile_id not in store["rejected"]:
            store["rejected"].append(tile_id)

    update(fn)


def hide(tile_id: str) -> None:
    """Hide a starter tile, or delete a learned one (and never propose it again)."""
    def fn(store):
        if any(t["id"] == tile_id for t in store["tiles"]):
            store["tiles"] = [t for t in store["tiles"] if t["id"] != tile_id]
            if tile_id not in store["rejected"]:
                store["rejected"].append(tile_id)
        elif tile_id not in store["hidden"]:
            store["hidden"].append(tile_id)

    update(fn)


def unhide_all() -> None:
    def fn(store):
        store["hidden"] = []

    update(fn)


# ---------- Proposals (AI) ----------

PROPOSAL_SCHEMA = {
    "name": "tile_proposals",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["proposals"],
        "properties": {
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "emoji", *LABEL_COLS, "reason", "evidence"],
                    "properties": {
                        "id": {"type": "string"},
                        "emoji": {"type": "string"},
                        **{c: {"type": "string"} for c in LABEL_COLS},
                        "reason": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        },
    },
}

PROPOSAL_PROMPT = """\
You keep a personal picture-tile board (AAC) up to date for {name}, who cannot speak clearly and replies by tapping tiles.
The board started with tiles most people need. Your job: notice what is specific to {name}'s own life and propose up to {max} NEW tiles
that would let {name} say things they need often but the board lacks.

Good tiles are concrete and personal: a hobby (parachute, cricket bat), a food from their culture (pasta, dosa),
equipment they rely on (crutch, wheelchair, hearing aid), a person they talk about often (first name only), a place they go.

EVIDENCE RULES (strict)
- Only propose a concept that appears in at least 2 separate log entries, OR that {name} typed themselves
  (typing is slow and hard for {name}, so a typed word the board lacks is strong evidence).
- evidence: copy the exact log texts that mention it, word for word.
- Never propose something already on the board (or a synonym of it), or anything in the rejected list.
- Never propose phone numbers, addresses, money amounts, passwords, surnames or medical diagnoses.
- Propose nothing if nothing clearly qualifies. An empty list is a good answer.

For each tile:
- id: short snake_case English, e.g. "parachute".
- emoji: ONE emoji that a person would recognise for it.
- labels: label_en (English), label_hinglish (Hindi-English in Roman script), label_hi (Hindi, Devanagari),
  label_kn (Kannada script), label_mr (Marathi, Devanagari). 1-3 words each.
- reason: one short English sentence for {name} and their family, e.g. "Parachutes came up in 3 conversations."

ALREADY ON THE BOARD (id: English): {board}
REJECTED BEFORE (never propose again): {rejected}
"""


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def validate_proposals(raw: object, *, events: list[dict], existing: dict[str, str],
                       rejected: set[str], pending_ids: set[str]) -> list[dict]:
    """Keep only well-formed, new, evidence-backed proposals."""
    if not isinstance(raw, dict) or not isinstance(raw.get("proposals"), list):
        raise ValueError("proposal output is not {'proposals': [...]}")
    texts = [(_norm(e["text"]), e["kind"]) for e in events]
    existing_labels = {_norm(v) for v in existing.values()}
    out = []
    for p in raw["proposals"]:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id", "")).strip().lower()
        if not ID_RE.match(pid) or pid in existing or pid in rejected or pid in pending_ids:
            continue
        labels = {c: " ".join(str(p.get(c, "")).split()) for c in LABEL_COLS}
        if not all(0 < len(v) <= 30 for v in labels.values()):
            continue
        if _norm(labels["label_en"]) in existing_labels:
            continue
        emoji = str(p.get("emoji", "")).strip()
        if not emoji or len(emoji) > 8 or emoji.isascii():
            continue
        # Evidence must really be in the log: >= 2 distinct entries, or anything the user typed.
        matched = set()
        typed = False
        for quote in p.get("evidence") or []:
            q = _norm(str(quote))
            if len(q) < 3:
                continue
            for i, (text, kind) in enumerate(texts):
                if q in text or (len(text) >= 3 and text in q):
                    matched.add(i)
                    typed = typed or kind == "typed"
        if len(matched) < 2 and not typed:
            log.info("proposal %r dropped: evidence not found in log", pid)
            continue
        reason = " ".join(str(p.get("reason", "")).split())[:160]
        out.append({"id": pid, "emoji": emoji, **labels, "reason": reason})
        pending_ids = pending_ids | {pid}
        if len(out) == MAX_PROPOSALS:
            break
    return out


async def propose(*, profile: dict, board: list[dict], post_chat, model: str, base: str, key: str,
                  force: bool = False) -> list[dict]:
    """Analyse the log if enough new entries arrived; return all pending proposals."""
    store = load()
    new_events = store["events_total"] - store["analysed_at"]
    if store["pending"] and len(store["pending"]) >= MAX_PENDING:
        return store["pending"]
    if not force and new_events < MIN_NEW_EVENTS:
        return store["pending"]
    events = store["events"][-EVENTS_FOR_ANALYSIS:]
    if len(events) < 2:
        return store["pending"]

    existing = {r["id"]: r["label_en"] for r in board}
    for t in store["tiles"]:
        existing[t["id"]] = t["label_en"]
    rejected = set(store["rejected"])
    system = PROPOSAL_PROMPT.format(
        name=profile.get("name", "the user"),
        max=MAX_PROPOSALS,
        board=", ".join(f"{k}: {v}" for k, v in existing.items()),
        rejected=", ".join(sorted(rejected)) or "none",
    )
    log_text = "\n".join(f"[{e['kind']}] {e['text']}" for e in events)
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "CONVERSATION LOG (oldest first; partner = someone talking to "
                                        f"{profile.get('name', 'the user')}, spoke = what they said via the app, "
                                        "typed = what they typed):\n" + log_text},
        ],
        "response_format": {"type": "json_schema", "json_schema": PROPOSAL_SCHEMA},
        "max_completion_tokens": 1500,
        "reasoning_effort": "low",
    }
    t0 = time.perf_counter()
    status, payload = await post_chat(f"{base}/chat/completions", body, key)
    if status != 200 or not isinstance(payload, dict):
        raise ValueError(f"LLM HTTP {status}")
    raw = json.loads(payload["choices"][0]["message"].get("content") or "")
    fresh = validate_proposals(raw, events=events, existing=existing, rejected=rejected,
                               pending_ids={p["id"] for p in store["pending"]})
    log.info("learning: %d new proposal(s) from %d log entries, took %.0fms",
             len(fresh), len(events), (time.perf_counter() - t0) * 1000)

    def fn(s):
        s["pending"] = (s["pending"] + fresh)[:MAX_PENDING]
        s["analysed_at"] = s["events_total"]
        return s["pending"]

    return update(fn)
