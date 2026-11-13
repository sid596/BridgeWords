"""BRIDGEWORDS backend. Step 1: serve the page and the symbol board."""
import csv
import json
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATIC = ROOT / "static"

load_dotenv(ROOT / ".env")

# Hinglish is written in Latin script, so it shows the English labels.
LANGUAGES = {
    "hinglish": {"name": "Hinglish", "code": "hi-IN", "label_col": "label_en"},
    "hi": {"name": "हिन्दी", "code": "hi-IN", "label_col": "label_hi"},
    "en": {"name": "English", "code": "en-IN", "label_col": "label_en"},
    "kn": {"name": "ಕನ್ನಡ", "code": "kn-IN", "label_col": "label_kn"},
    "mr": {"name": "मराठी", "code": "mr-IN", "label_col": "label_mr"},
}

# Urgent tiles speak immediately, with no LLM step (hard rule 2).
URGENT_IDS = ("help", "breathing")

app = FastAPI(title="BRIDGEWORDS")


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


@lru_cache
def load_profile() -> dict:
    with open(DATA / "profile.json", encoding="utf-8") as f:
        return json.load(f)


def default_lang() -> str:
    lang = load_profile().get("language", "en")
    return lang if lang in LANGUAGES else "en"


@app.get("/api/symbols")
def get_symbols(lang: str | None = None):
    lang = lang or default_lang()
    if lang not in LANGUAGES:
        raise HTTPException(400, f"Unknown language '{lang}'")
    col = LANGUAGES[lang]["label_col"]

    tiles, categories = [], []
    for row in load_symbols():
        if row["category"] not in categories:
            categories.append(row["category"])
        tiles.append({
            "id": row["id"],
            "category": row["category"],
            "label": row[col] or row["label_en"],
            "label_en": row["label_en"],
            "emoji": row["emoji"],
            "urgent": row["id"] in URGENT_IDS,
        })

    return {
        "lang": lang,
        "languages": [{"id": k, "name": v["name"]} for k, v in LANGUAGES.items()],
        "categories": categories,
        "tiles": tiles,
    }


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
