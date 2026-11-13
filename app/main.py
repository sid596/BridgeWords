"""BRIDGEWORDS backend.

GET  /api/symbols   tiles in the active language (+ fixed phrases for urgent/Wait tiles)
POST /api/speak     Gnani Timbre, cached by text + language + model + voice
POST /api/suggest   LLM suggestions (quick replies, sentences or a clarifying question)
POST /api/listen    Gnani Prisma transcription of the partner's speech
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
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import gnani, llm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATIC = ROOT / "static"
TTS_CACHE_DIR = ROOT / "cache" / "tts"

load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bridgewords")

# stt: Prisma v3.0 language list (the partner often mixes in English).
# tts: Timbre language ("hi-en" is Hinglish). browser: device-voice fallback for urgent tiles.
# v25_voice: per-language voice, used only if profile.json selects timbre-v2.5.
LANGUAGES = {
    "hinglish": {"name": "Hinglish", "label_col": "label_en", "stt": "hi-IN,en-IN", "tts": "hi-en",
                 "browser": "hi-IN", "v25_voice": "Poorvi",
                 "llm": "Hinglish (Hindi-English mix written in Roman script, the way people text in India)"},
    "hi": {"name": "हिन्दी", "label_col": "label_hi", "stt": "hi-IN,en-IN", "tts": "hi-IN",
           "browser": "hi-IN", "v25_voice": "Yashvi", "llm": "Hindi, in Devanagari script"},
    "en": {"name": "English", "label_col": "label_en", "stt": "en-IN,hi-IN", "tts": "en-IN",
           "browser": "en-IN", "v25_voice": "Pranav", "llm": "simple Indian English"},
    "kn": {"name": "ಕನ್ನಡ", "label_col": "label_kn", "stt": "kn-IN,en-IN", "tts": "kn-IN",
           "browser": "kn-IN", "v25_voice": "Kavin",
           "llm": "Kannada, in Kannada script (English words commonly used in Bengaluru are fine)"},
    "mr": {"name": "मराठी", "label_col": "label_mr", "stt": "mr-IN,en-IN", "tts": "mr-IN",
           "browser": "mr-IN", "v25_voice": "Ishaan", "llm": "Marathi, in Devanagari script"},
}

# Urgent tiles speak immediately, with no LLM step (hard rule 2).
URGENT_IDS = ("help", "breathing")

DEFAULT_TTS_MODEL = "timbre-v3.0"
DEFAULT_V3_VOICE = "Devansh"
MAX_SPEAK_CHARS = 400
MAX_AUDIO_BYTES = 5 * 1024 * 1024   # 30 s of 16 kHz mono WAV is ~1 MB
MAX_HISTORY = 6

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


def check_lang(lang: str | None) -> str:
    lang = lang or default_lang()
    if lang not in LANGUAGES:
        raise HTTPException(400, f"Unknown language '{lang}'")
    return lang


def label_for(row: dict, lang: str) -> str:
    return row[LANGUAGES[lang]["label_col"]] or row["label_en"]


def tts_voice(lang: str) -> tuple[str, str]:
    """(model, voice) from profile.json."""
    profile = load_profile()
    model = profile.get("tts_model") or DEFAULT_TTS_MODEL
    voice = (profile.get("voices") or {}).get(lang) or profile.get("voice")
    if not voice:
        voice = LANGUAGES[lang]["v25_voice"] if model == "timbre-v2.5" else DEFAULT_V3_VOICE
    return model, voice


# ---------- Symbols ----------

@app.get("/api/symbols")
def get_symbols(lang: str | None = None):
    lang = check_lang(lang)
    phrases = load_json("phrases.json")

    tiles, categories = [], []
    for row in load_symbols():
        if row["category"] not in categories:
            categories.append(row["category"])
        tile = {
            "id": row["id"],
            "category": row["category"],
            "label": label_for(row, lang),
            "label_en": row["label_en"],
            "emoji": row["emoji"],
            "urgent": row["id"] in URGENT_IDS,
        }
        phrase = phrases.get(row["id"])
        if isinstance(phrase, dict):
            tile["say"] = phrase.get(lang) or tile["label"]
        tiles.append(tile)

    return {
        "lang": lang,
        "languages": [{"id": k, "name": v["name"], "code": v["browser"]} for k, v in LANGUAGES.items()],
        "categories": categories,
        "tiles": tiles,
    }


# ---------- Speech out (Timbre) ----------

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
    model, voice = tts_voice(lang)
    raw = f"{model}|{LANGUAGES[lang]['tts']}|{voice}|{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def require_gnani_key():
    if not os.environ.get("GNANI_API_KEY"):
        raise HTTPException(503, "GNANI_API_KEY is not set in .env.")


def gnani_error(service: str, e: Exception) -> HTTPException:
    if isinstance(e, httpx.HTTPStatusError):
        log.warning("%s HTTP %s", service, e.response.status_code)
        return HTTPException(502, f"{service} returned HTTP {e.response.status_code}.")
    if isinstance(e, httpx.HTTPError):
        log.warning("%s unreachable: %s", service, type(e).__name__)
        return HTTPException(502, f"Could not reach Gnani {service}.")
    return HTTPException(502, str(e))


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

        require_gnani_key()
        model, voice = tts_voice(lang)
        code = LANGUAGES[lang]["tts"]
        t0 = time.perf_counter()
        try:
            audio = gnani.synthesize(text, language=code, voice=voice, model=model)
        except (httpx.HTTPError, ValueError) as e:
            raise gnani_error("Timbre", e)
        log.info("TTS miss model=%s lang=%s voice=%s chars=%d took=%.0fms",
                 model, code, voice, len(text), (time.perf_counter() - t0) * 1000)

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


# ---------- Suggestions (LLM) ----------

class Clarification(BaseModel):
    question: str = Field(max_length=300)
    answer: str = Field(max_length=100)


class SuggestRequest(BaseModel):
    history: list[str] = Field(default_factory=list, max_length=50)
    tiles: list[str] = Field(default_factory=list, max_length=30)
    typed: str = Field(default="", max_length=300)
    lang: str | None = None
    clarification: Clarification | None = None


def fallback(selected: list[dict], typed: str) -> dict:
    """No LLM: join what the user chose, so they can still speak it."""
    words = [t["label"] for t in selected] + ([typed] if typed else [])
    if words:
        return {"quick_replies": [], "sentences": [", ".join(words)], "clarify": None, "fallback": True}
    return {"quick_replies": ["yes", "no", "dont_know", "repeat"], "sentences": [], "clarify": None,
            "fallback": True}


@app.post("/api/suggest")
async def suggest(req: SuggestRequest):
    lang = check_lang(req.lang)
    rows = load_symbols()
    by_id = {r["id"]: r for r in rows}
    selected = [{"id": t, "label": label_for(by_id[t], lang), "meaning": by_id[t]["label_en"]}
                for t in dict.fromkeys(req.tiles) if t in by_id]
    typed = " ".join(req.typed.split())
    history = [" ".join(h.split())[:500] for h in req.history if h.strip()][-MAX_HISTORY:]

    if not selected and not typed and not history:
        return {"quick_replies": [], "sentences": [], "clarify": None, "fallback": False}

    t0 = time.perf_counter()
    try:
        result = await llm.suggest(
            profile=load_profile(),
            language_desc=LANGUAGES[lang]["llm"],
            catalogue=[{"id": r["id"], "label_en": r["label_en"]} for r in rows],
            history=history,
            selected=selected,
            typed=typed,
            clarification=req.clarification.model_dump() if req.clarification else None,
        )
        result["fallback"] = False
    except Exception as e:  # any LLM problem: never leave the user without words
        log.warning("LLM fallback (%s): %s", type(e).__name__, str(e)[:200])
        result = fallback(selected, typed)
    result["ms"] = round((time.perf_counter() - t0) * 1000)
    return result


# ---------- Listening (Prisma) ----------

@app.post("/api/listen")
def listen(audio: UploadFile = File(...), lang: str | None = Form(None)):
    lang = check_lang(lang)
    data = audio.file.read(MAX_AUDIO_BYTES + 1)
    if not data:
        raise HTTPException(400, "Empty audio.")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio too long (max 30 s).")
    require_gnani_key()

    codes = LANGUAGES[lang]["stt"]
    t0 = time.perf_counter()
    try:
        result = gnani.transcribe(data, filename=audio.filename or "partner.wav",
                                  language=codes, fmt="codemix")
    except (httpx.HTTPError, ValueError) as e:
        raise gnani_error("Prisma", e)
    ms = round((time.perf_counter() - t0) * 1000)
    transcript = " ".join(result["transcript"].split())
    language = result.get("detected_language_code") or codes.split(",")[0]
    log.info("STT lang=%s detected=%s chars=%d took=%dms", codes, language, len(transcript), ms)
    return {"transcript": transcript, "language": language, "ms": ms}


# ---------- Page ----------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
