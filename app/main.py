"""BRIDGEWORDS backend.

Step 1: serve the page and the symbol board.
Step 2: /api/speak (Gnani Timbre, cached by text + language + voice), urgent phrases.
"""
import csv
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import gnani

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATIC = ROOT / "static"
TTS_CACHE_DIR = ROOT / "cache" / "tts"

load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bridgewords")

# Hinglish is written in Latin script, so it shows the English labels.
LANGUAGES = {
    "hinglish": {"name": "Hinglish", "code": "hi-IN", "label_col": "label_en", "voice": "Poorvi"},
    "hi": {"name": "हिन्दी", "code": "hi-IN", "label_col": "label_hi", "voice": "Yashvi"},
    "en": {"name": "English", "code": "en-IN", "label_col": "label_en", "voice": "Pranav"},
    "kn": {"name": "ಕನ್ನಡ", "code": "kn-IN", "label_col": "label_kn", "voice": "Kavin"},
    "mr": {"name": "मराठी", "code": "mr-IN", "label_col": "label_mr", "voice": "Ishaan"},
}

# Urgent tiles speak immediately, with no LLM step (hard rule 2).
URGENT_IDS = ("help", "breathing")

TTS_MODEL = "timbre-v2.5"   # must match app/gnani.py
MAX_SPEAK_CHARS = 400

app = FastAPI(title="BRIDGEWORDS")


# ---------- Data ----------

REQUIRED_COLUMNS = {"id", "category", "label_en", "label_hi", "label_kn", "label_mr", "emoji"}
_symbols_cache: dict = {"mtime": None, "rows": []}


def load_symbols() -> list[dict]:
    """Re-read symbols.csv whenever it changes, so edits show on a browser refresh."""
    path = DATA / "symbols.csv"
    mtime = path.stat().st_mtime
    if _symbols_cache["mtime"] != mtime:
        try:
            # utf-8-sig also accepts the BOM that Excel's "CSV UTF-8" export adds.
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
                if missing:
                    raise HTTPException(500, f"symbols.csv is missing columns: {sorted(missing)}")
                rows = [row for row in reader if (row.get("id") or "").strip()]
        except UnicodeDecodeError:
            raise HTTPException(500, "symbols.csv is not UTF-8. Re-save it as 'CSV UTF-8'.")
        _symbols_cache.update(mtime=mtime, rows=rows)
    return _symbols_cache["rows"]


def load_json(name: str) -> dict:
    """Small JSON data files; read on each call so edits apply without a restart."""
    with open(DATA / name, encoding="utf-8") as f:
        return json.load(f)


def load_profile() -> dict:
    return load_json("profile.json")


def default_lang() -> str:
    lang = load_profile().get("language", "en")
    return lang if lang in LANGUAGES else "en"


def voice_for(lang: str) -> str:
    return load_profile().get("voices", {}).get(lang) or LANGUAGES[lang]["voice"]


def check_lang(lang: str | None) -> str:
    lang = lang or default_lang()
    if lang not in LANGUAGES:
        raise HTTPException(400, f"Unknown language '{lang}'")
    return lang


# ---------- Symbols ----------

@app.get("/api/symbols")
def get_symbols(lang: str | None = None):
    lang = check_lang(lang)
    col = LANGUAGES[lang]["label_col"]
    urgent_phrases = load_json("urgent.json")

    tiles, categories = [], []
    for row in load_symbols():
        if row["category"] not in categories:
            categories.append(row["category"])
        tile = {
            "id": row["id"],
            "category": row["category"],
            "label": row[col] or row["label_en"],
            "label_en": row["label_en"],
            "emoji": row["emoji"],
            "urgent": row["id"] in URGENT_IDS,
        }
        if tile["urgent"]:
            tile["say"] = urgent_phrases.get(row["id"], {}).get(lang) or tile["label"]
        tiles.append(tile)

    return {
        "lang": lang,
        "languages": [{"id": k, "name": v["name"], "code": v["code"]} for k, v in LANGUAGES.items()],
        "categories": categories,
        "tiles": tiles,
    }


# ---------- Speech (Timbre) ----------

class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_SPEAK_CHARS)
    lang: str | None = None


_tts_memory: dict[str, bytes] = {}
_tts_locks: dict[str, threading.Lock] = {}
_tts_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _tts_locks_guard:
        return _tts_locks.setdefault(key, threading.Lock())


def tts_cache_key(text: str, lang: str) -> str:
    raw = f"{TTS_MODEL}|{LANGUAGES[lang]['code']}|{voice_for(lang)}|{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def synthesize_cached(text: str, lang: str) -> tuple[bytes, bool]:
    """Return (wav, cache_hit). Each text+language+voice is billed at most once, ever."""
    key = tts_cache_key(text, lang)
    if key in _tts_memory:
        return _tts_memory[key], True

    # One lock per phrase, so two simultaneous taps never pay for the same audio twice.
    with _lock_for(key):
        if key in _tts_memory:
            return _tts_memory[key], True
        path = TTS_CACHE_DIR / f"{key}.wav"
        if path.exists():
            _tts_memory[key] = path.read_bytes()
            return _tts_memory[key], True

        if not os.environ.get("GNANI_API_KEY"):
            raise HTTPException(503, "GNANI_API_KEY is not set in .env, so the app cannot speak yet.")

        code, voice = LANGUAGES[lang]["code"], voice_for(lang)
        t0 = time.perf_counter()
        try:
            audio = gnani.synthesize(text, language=code, voice=voice)
        except httpx.HTTPStatusError as e:
            log.warning("Timbre HTTP %s for lang=%s voice=%s", e.response.status_code, code, voice)
            raise HTTPException(502, f"Timbre returned HTTP {e.response.status_code}.")
        except httpx.HTTPError as e:
            log.warning("Timbre unreachable: %s", type(e).__name__)
            raise HTTPException(502, "Could not reach Gnani Timbre.")
        except ValueError as e:
            raise HTTPException(502, str(e))
        log.info("TTS miss lang=%s voice=%s chars=%d took=%.0fms",
                 code, voice, len(text), (time.perf_counter() - t0) * 1000)

        TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(audio)
        tmp.replace(path)
        _tts_memory[key] = audio
        return audio, False


@app.post("/api/speak")
def speak(req: SpeakRequest):
    lang = check_lang(req.lang)
    text = " ".join(req.text.split())
    if not text:
        raise HTTPException(400, "Nothing to speak.")
    audio, hit = synthesize_cached(text, lang)
    return Response(audio, media_type="audio/wav",
                    headers={"X-Cache": "hit" if hit else "miss", "Cache-Control": "no-store"})


# ---------- Page ----------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
