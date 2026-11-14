"""BRIDGEWORDS backend.

GET  /api/symbols   tiles in the active language (+ fixed phrases for urgent/Wait tiles)
POST /api/speak     Gnani Timbre, cached by text + language + model + voice
POST /api/suggest   LLM suggestions (quick replies, sentences or a clarifying question)
POST /api/listen    Gnani Prisma transcription of the partner's speech
/api/learn/*        personal board learning (AI proposes new tiles; the user decides)
/api/tiles/*        hide / restore tiles
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

from app import gnani, learning, llm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATIC = ROOT / "static"
TTS_CACHE_DIR = ROOT / "cache" / "tts"

ENV_PATH = ROOT / ".env"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bridgewords")


def env_problem() -> str | None:
    """Why the keys can't be read, in plain words (never includes key values)."""
    if not ENV_PATH.exists():
        for wrong in (".env.txt", "env", "env.txt", ".env.example.txt"):
            if (ROOT / wrong).exists():
                return f"Found '{wrong}' instead of '.env'. Rename it to exactly .env, then restart the server."
        return f"No .env file in {ROOT}. Copy .env.example to .env, add the keys, then restart the server."
    try:
        ENV_PATH.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return ".env is not saved as UTF-8 (Windows Notepad often uses UTF-16). Save it as UTF-8, then restart."
    return None


# Load keys only if the file is readable, so a badly saved .env can't crash the server.
if env_problem() is None:
    load_dotenv(ENV_PATH)


def report_keys() -> None:
    problem = env_problem()
    if problem:
        log.warning(problem)
    for name in ("GNANI_API_KEY", "OPENAI_API_KEY"):
        log.info("%s: %s", name, "set" if os.environ.get(name) else "MISSING")


report_keys()

# SPEECH: every language BRIDGEWORDS can hear, translate and speak (all 10 Gnani languages + Hinglish).
#   stt: Prisma v3.0 language list (partners often mix in English). tts: Timbre language ("hi-en" = Hinglish).
#   browser: device-voice fallback. v25_voice: only used if profile.json selects timbre-v2.5.
#   base: the underlying language (Hinglish is Hindi for "do they speak the same language?").
SPEECH = {
    "hinglish": {"name": "Hinglish", "stt": "hi-IN,en-IN", "tts": "hi-en", "browser": "hi-IN", "v25_voice": "Poorvi",
                 "base": "hi", "llm": "Hinglish (Hindi-English mix written in Roman script, the way people text in India)"},
    "hi": {"name": "हिन्दी", "stt": "hi-IN,en-IN", "tts": "hi-IN", "browser": "hi-IN", "v25_voice": "Yashvi",
           "base": "hi", "llm": "Hindi, in Devanagari script"},
    "en": {"name": "English", "stt": "en-IN,hi-IN", "tts": "en-IN", "browser": "en-IN", "v25_voice": "Pranav",
           "base": "en", "llm": "simple Indian English"},
    "kn": {"name": "ಕನ್ನಡ", "stt": "kn-IN,en-IN", "tts": "kn-IN", "browser": "kn-IN", "v25_voice": "Kavin",
           "base": "kn", "llm": "Kannada, in Kannada script (English words commonly used in Bengaluru are fine)"},
    "mr": {"name": "मराठी", "stt": "mr-IN,en-IN", "tts": "mr-IN", "browser": "mr-IN", "v25_voice": "Ishaan",
           "base": "mr", "llm": "Marathi, in Devanagari script"},
    "ta": {"name": "தமிழ்", "stt": "ta-IN,en-IN", "tts": "ta-IN", "browser": "ta-IN", "v25_voice": "Asmita",
           "base": "ta", "llm": "Tamil, in Tamil script"},
    "te": {"name": "తెలుగు", "stt": "te-IN,en-IN", "tts": "te-IN", "browser": "te-IN", "v25_voice": "Suhana",
           "base": "te", "llm": "Telugu, in Telugu script"},
    "ml": {"name": "മലയാളം", "stt": "ml-IN,en-IN", "tts": "ml-IN", "browser": "ml-IN", "v25_voice": "Riyaan",
           "base": "ml", "llm": "Malayalam, in Malayalam script"},
    "bn": {"name": "বাংলা", "stt": "bn-IN,en-IN", "tts": "bn-IN", "browser": "bn-IN", "v25_voice": "Dhruva",
           "base": "bn", "llm": "Bengali, in Bengali script"},
    "gu": {"name": "ગુજરાતી", "stt": "gu-IN,en-IN", "tts": "gu-IN", "browser": "gu-IN", "v25_voice": "Veera",
           "base": "gu", "llm": "Gujarati, in Gujarati script"},
    "pa": {"name": "ਪੰਜਾਬੀ", "stt": "pa-IN,en-IN", "tts": "pa-IN", "browser": "pa-IN", "v25_voice": "Zayan",
           "base": "pa", "llm": "Punjabi, in Gurmukhi script"},
}
# Prisma's detected_language_code -> speech language id
STT_CODE_TO_SPEECH = {v["tts"]: k for k, v in SPEECH.items() if k != "hinglish"}

# Writing system of each board language: suggestions the user can't read are dropped.
BOARD_SCRIPT = {"hinglish": "latin", "en": "latin", "hi": "devanagari", "mr": "devanagari", "kn": "kannada"}

# BOARD languages: the user reads and taps in one of these (tile labels exist for them).
# understands: languages the user follows without a translation (profile.json can add more).
LANGUAGES = {
    "hinglish": {"name": "Hinglish", "label_col": "label_hinglish", "understands": {"hi", "en"}},
    "hi": {"name": "हिन्दी", "label_col": "label_hi", "understands": {"hi"}},
    "en": {"name": "English", "label_col": "label_en", "understands": {"en"}},
    "kn": {"name": "ಕನ್ನಡ", "label_col": "label_kn", "understands": {"kn"}},
    "mr": {"name": "मराठी", "label_col": "label_mr", "understands": {"mr"}},
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


def check_speech_lang(lang: str | None) -> str:
    lang = lang or default_lang()
    if lang not in SPEECH:
        raise HTTPException(400, f"Unknown language '{lang}'")
    return lang


def understood_by_user(board_lang: str) -> set[str]:
    extra = {SPEECH[x]["base"] for x in load_profile().get("understands", []) if x in SPEECH}
    return LANGUAGES[board_lang]["understands"] | extra


def partner_plan(board_lang: str, partner_lang: str | None) -> tuple[str | None, bool]:
    """(language to speak replies in, whether to translate partner lines for the user)."""
    if not partner_lang or partner_lang not in SPEECH:
        return None, False
    base = SPEECH[partner_lang]["base"]
    spoken = partner_lang if base != SPEECH[board_lang]["base"] else None
    return spoken, base not in understood_by_user(board_lang)


def label_for(row: dict, lang: str) -> str:
    return row.get(LANGUAGES[lang]["label_col"]) or row["label_en"]


def board_rows() -> list[dict]:
    """Starter tiles + the user's learned tiles, minus hidden ones. Urgent tiles can't be hidden."""
    hidden = learning.hidden_ids()
    rows = [r for r in load_symbols() if r["id"] not in hidden or r["id"] in URGENT_IDS]
    known = {r["id"] for r in load_symbols()}
    return rows + [r for r in learning.learned_rows() if r["id"] not in known]


def learning_enabled() -> bool:
    return load_profile().get("learning", True) is not False


def tts_voice(lang: str) -> tuple[str, str]:
    """(model, voice) from profile.json."""
    profile = load_profile()
    model = profile.get("tts_model") or DEFAULT_TTS_MODEL
    voice = (profile.get("voices") or {}).get(lang) or profile.get("voice")
    if not voice:
        voice = SPEECH[lang]["v25_voice"] if model == "timbre-v2.5" else DEFAULT_V3_VOICE
    return model, voice


# ---------- Symbols ----------

@app.get("/api/symbols")
def get_symbols(lang: str | None = None):
    lang = check_lang(lang)
    phrases = load_json("phrases.json")

    tiles, categories = [], []
    for row in board_rows():
        if row["category"] not in categories:
            categories.append(row["category"])
        tile = {
            "id": row["id"],
            "category": row["category"],
            "label": label_for(row, lang),
            "label_en": row["label_en"],
            "emoji": row["emoji"],
            "urgent": row["id"] in URGENT_IDS,
            "learned": row["category"] == learning.MY_TILES,
        }
        phrase = phrases.get(row["id"])
        if isinstance(phrase, dict):
            tile["say"] = phrase.get(lang) or tile["label"]
            # Same phrase in other languages, so an urgent tile can speak the partner's language.
            tile["say_by_lang"] = {k: v for k, v in phrase.items() if k in SPEECH and isinstance(v, str) and v}
        tiles.append(tile)

    if learning.MY_TILES in categories:  # the user's own tiles come first
        categories.remove(learning.MY_TILES)
        categories.insert(0, learning.MY_TILES)

    ui_all = load_json("ui.json")
    ui = {**ui_all.get("en", {}), **ui_all.get(lang, {})}
    ui["categories"] = {**ui_all.get("en", {}).get("categories", {}), **ui_all.get(lang, {}).get("categories", {})}

    return {
        "lang": lang,
        "languages": [{"id": k, "name": v["name"], "code": SPEECH[k]["browser"]} for k, v in LANGUAGES.items()],
        "partner_languages": [{"id": k, "name": v["name"], "code": v["browser"]} for k, v in SPEECH.items()],
        "categories": categories,
        "tiles": tiles,
        "hidden_count": len(learning.hidden_ids() - set(URGENT_IDS)),
        "ui": ui,
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
    raw = f"{model}|{SPEECH[lang]['tts']}|{voice}|{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def require_gnani_key():
    if not os.environ.get("GNANI_API_KEY"):
        hint = env_problem() or ("GNANI_API_KEY is missing or empty in .env (check the spelling). "
                                 "Restart the server after editing .env.")
        raise HTTPException(503, f"GNANI_API_KEY is not set. {hint}")


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
        code = SPEECH[lang]["tts"]
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
    lang = check_speech_lang(req.lang)  # any language: replies may be spoken in the partner's language
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
    partner_lang: str | None = None   # what the partner speaks, if not the user's own language
    clarification: Clarification | None = None


def fallback(selected: list[dict], typed: str, spoken_lang: str | None = None, by_id=None) -> dict:
    """No LLM: join what the user chose, so they can still speak it."""
    base = {"quick_replies": [], "sentences": [], "spoken": [], "clarify": None, "translation": "", "fallback": True}
    words = [t["label"] for t in selected] + ([typed] if typed else [])
    if not words:
        return {**base, "quick_replies": ["yes", "no", "dont_know", "repeat"]}
    base["sentences"] = [", ".join(words)]
    if spoken_lang in LANGUAGES and by_id:  # tile labels exist in the partner's language
        spoken = [label_for(by_id[t["id"]], spoken_lang) for t in selected] + ([typed] if typed else [])
        base["spoken"] = [", ".join(spoken)]
    return base


@app.post("/api/suggest")
async def suggest(req: SuggestRequest):
    lang = check_lang(req.lang)
    spoken_lang, translate = partner_plan(lang, req.partner_lang)
    rows = board_rows()
    by_id = {r["id"]: r for r in rows}
    selected = [{"id": t, "label": label_for(by_id[t], lang), "meaning": by_id[t]["label_en"]}
                for t in dict.fromkeys(req.tiles) if t in by_id]
    typed = " ".join(req.typed.split())
    history = [" ".join(h.split())[:500] for h in req.history if h.strip()][-MAX_HISTORY:]
    # Translate only right after the partner speaks (the reply row); building a reply needs no translation.
    translate = translate and not selected and not typed and bool(history)

    if not selected and not typed and not history:
        return {"quick_replies": [], "sentences": [], "spoken": [], "clarify": None, "translation": "",
                "fallback": False, "spoken_lang": spoken_lang}
    if typed and learning_enabled():
        learning.add_event("typed", typed)  # words the board lacks are the best learning signal

    t0 = time.perf_counter()
    try:
        result = await llm.suggest(
            profile=load_profile(),
            language_desc=SPEECH[lang]["llm"],
            script=BOARD_SCRIPT[lang],
            partner_desc=SPEECH[req.partner_lang]["llm"] if (spoken_lang or translate) else None,
            want_spoken=bool(spoken_lang),
            want_translation=translate,
            catalogue=[{"id": r["id"], "label_en": r["label_en"]} for r in rows],
            history=history,
            selected=selected,
            typed=typed,
            clarification=req.clarification.model_dump() if req.clarification else None,
        )
        result["fallback"] = False
    except Exception as e:  # any LLM problem: never leave the user without words
        log.warning("LLM fallback (%s): %s", type(e).__name__, str(e)[:200])
        result = fallback(selected, typed, spoken_lang, by_id)
    result["spoken_lang"] = spoken_lang if result["spoken"] else None
    result["ms"] = round((time.perf_counter() - t0) * 1000)
    return result


# ---------- Listening (Prisma) ----------

@app.post("/api/listen")
def listen(audio: UploadFile = File(...), lang: str | None = Form(None), partner: str | None = Form(None)):
    """partner: "" = same as the user's language, "auto" = detect, or a speech language id."""
    lang = check_lang(lang)
    data = audio.file.read(MAX_AUDIO_BYTES + 1)
    if not data:
        raise HTTPException(400, "Empty audio.")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio too long (max 30 s).")
    require_gnani_key()

    if partner == "auto":
        codes = "indic-auto"
    elif partner:
        codes = SPEECH[check_speech_lang(partner)]["stt"]
    else:
        codes = SPEECH[lang]["stt"]
    t0 = time.perf_counter()
    try:
        result = gnani.transcribe(data, filename=audio.filename or "partner.wav",
                                  language=codes, fmt="codemix")
    except (httpx.HTTPError, ValueError) as e:
        raise gnani_error("Prisma", e)
    ms = round((time.perf_counter() - t0) * 1000)
    transcript = " ".join(result["transcript"].split())
    language = result.get("detected_language_code") or (codes.split(",")[0] if codes != "indic-auto" else "")
    log.info("STT lang=%s detected=%s chars=%d took=%dms", codes, language, len(transcript), ms)
    if transcript and learning_enabled():
        learning.add_event("partner", transcript)
    # Which speech language the partner used (for auto mode and translation).
    if partner and partner != "auto":
        partner_lang = partner
    else:
        partner_lang = STT_CODE_TO_SPEECH.get(language) or (lang if not partner else None)
    return {"transcript": transcript, "language": language, "partner_lang": partner_lang, "ms": ms}


# ---------- Personal board learning ----------

class SpokeEvent(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_SPEAK_CHARS)


class TileRef(BaseModel):
    id: str = Field(min_length=1, max_length=40)


def pending_view(lang: str) -> list[dict]:
    return [{"id": p["id"], "emoji": p["emoji"], "label": label_for(p, lang), "reason": p.get("reason", "")}
            for p in learning.load()["pending"]]


@app.post("/api/learn/spoke")
def learn_spoke(ev: SpokeEvent):
    """The user approved and spoke this sentence."""
    if learning_enabled():
        learning.add_event("spoke", ev.text)
    return {"ok": True}


@app.get("/api/learn")
def learn_status(lang: str | None = None):
    lang = check_lang(lang)
    store = learning.load()
    return {"enabled": learning_enabled(), "pending": pending_view(lang),
            "log_entries": len(store["events"]), "learned_tiles": len(store["tiles"])}


@app.post("/api/learn/propose")
async def learn_propose(lang: str | None = None, force: bool = False):
    """Ask the AI for new tile ideas (only if enough new conversation arrived). Never adds tiles itself."""
    lang = check_lang(lang)
    if not learning_enabled():
        return {"pending": []}
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        try:
            await learning.propose(
                profile=load_profile(),
                board=load_symbols(),
                post_chat=llm.post_chat,
                model=os.environ.get("LLM_MODEL", "gpt-5.4-mini"),
                base=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
                key=key,
                force=force,
            )
        except Exception as e:  # learning is a background nicety: never break the app
            log.warning("learning failed (%s): %s", type(e).__name__, str(e)[:200])
    return {"pending": pending_view(lang)}


@app.post("/api/learn/accept")
def learn_accept(ref: TileRef):
    tile = learning.accept(ref.id)
    if not tile:
        raise HTTPException(404, "No such tile idea.")
    return {"ok": True}


@app.post("/api/learn/reject")
def learn_reject(ref: TileRef):
    learning.reject(ref.id)
    return {"ok": True}


@app.post("/api/learn/forget")
def learn_forget():
    """Wipe the conversation log and pending ideas (accepted tiles stay)."""
    learning.forget()
    return {"ok": True}


@app.post("/api/tiles/hide")
def tiles_hide(ref: TileRef):
    if ref.id in URGENT_IDS:
        raise HTTPException(400, "Urgent tiles can't be hidden.")
    learning.hide(ref.id)
    return {"ok": True}


@app.post("/api/tiles/unhide")
def tiles_unhide():
    learning.unhide_all()
    return {"ok": True}


# ---------- Demo mode (?demo=1) ----------

class PartnerLine(BaseModel):
    text: str = Field(min_length=1, max_length=500)


# A synthetic week of conversation (fictional), to show board learning in a demo.
DEMO_WEEK = [
    ("partner", "Aarav, Sunday ko skydiving club jaana hai na?"),
    ("partner", "Tumhara parachute bag dry ho gaya?"),
    ("spoke", "Haan, mujhe skydiving jaana hai."),
    ("partner", "Crutch kahan rakhi hai? Bahar chalna hai."),
    ("partner", "Chai piyoge?"),
    ("typed", "pasta"),
    ("partner", "Aaj dinner mein pasta banau ya roti?"),
    ("partner", "Kal zoo chalein?"),
    ("partner", "Crutch le lo, stairs hain."),
    ("spoke", "Mujhe parachute dekhna hai."),
    ("partner", "Theek hai, so jao ab."),
]


@app.post("/api/demo/partner")
def demo_partner(line: PartnerLine):
    """A partner line typed in demo mode is treated like one Prisma heard."""
    if learning_enabled():
        learning.add_event("partner", line.text)
    return {"ok": True}


@app.post("/api/demo/seed")
async def demo_seed(lang: str | None = None):
    """Load the synthetic week and ask for tile ideas straight away."""
    for kind, text in DEMO_WEEK:
        learning.add_event(kind, text)
    return await learn_propose(lang=lang, force=True)


# ---------- Page + installable app ----------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    # Served from the root so it can make the whole app work offline.
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
