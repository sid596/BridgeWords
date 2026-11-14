# BRIDGEWORDS: project brief for Claude Code

Read this file before doing anything. It is the spec.

## What we are building

A tablet-first web app for people who can't speak clearly but can tap. It **listens** to the conversation partner, turns the board into replies that fit what was just said, builds full sentences from a few taps, **asks instead of guessing** when the meaning is unclear, and **speaks only the reply the user approves**, in their language.

This is an entry for Gnani AI's Great Indian AI Internship Challenge. **Deadline: Sunday 15 Nov 2026, 18:00 IST.** Build the smallest version that demos well. Polish beats features.

## Hard rules

1. **Nothing is ever spoken without the user tapping "Speak".** No auto-speak.
2. **Urgent tiles (`help`, `breathing`) speak immediately**, with no LLM step and no network dependency apart from TTS. Cache their audio at startup.
3. When the tiles leave the meaning unclear (e.g. hospital + pain + tomorrow), return a **clarifying yes/no question**, not a guessed sentence.
4. **Synthetic data only.** No real names, numbers or recordings in the repo.
5. **Secrets only in `.env`** (git-ignored). Never commit keys or phone numbers. Never print keys in logs.
6. Every suggestion row also shows **Something else** and **Wait**, so the user is never boxed in.

## Stack (keep it boring)

- **Backend:** Python 3.15 (latest; venv in `.venv`, built from Homebrew `python@3.15`), FastAPI, httpx, python-dotenv, uvicorn.
- **Frontend:** a single `static/index.html` + `static/app.js` + `static/style.css`. Vanilla JS, no build step, served by FastAPI.
- **Speech:** Gnani Prisma (STT) and Gnani Timbre (TTS) over REST.
- **LLM:** OpenAI Chat Completions with JSON output (model from `LLM_MODEL`, e.g. `gpt-5.4-mini`).
- **Data:** `data/symbols.csv` (tiles with en/hi/kn/mr labels) and `data/profile.json` (the demo user's name, language, voice, saved address and personal meanings). No database.

## Reuse from the HMH repo

`HMH/voice/gnani.py` already has working Prisma and Timbre adapters (base URL `https://api.vachana.ai`, header `X-API-Key-ID`, STT `POST /stt/v3` with model `prisma-v3.0`, TTS `POST /api/v1/tts/inference` with model `timbre-v2.5`). Copy that file into `app/gnani.py` and keep its behaviour, including no automatic retries. Check https://docs.gnani.ai for anything new (voice cloning, streaming).

Timbre voices that HMH used per language: Hindi `Yashvi`, Hinglish `Poorvi`, English `Pranav`, Marathi `Ishaan`, Kannada `Kavin`. Language codes: `hi-IN`, `en-IN`, `kn-IN`, `mr-IN`.

**Updated 2026-11-13 (docs re-checked):** we use **Timbre v3.0** (`timbre-v3.0`). Its 13 voices speak every language, so the user keeps one voice (set in `data/profile.json`, default `Devansh`). Hinglish TTS uses language `hi-en`. Prisma v3.0 gets language lists (`hi-IN,en-IN`, `kn-IN,en-IN`, …) with `format=codemix`. Prisma does **not** accept WebM, so the browser records PCM and sends 16 kHz WAV instead of using MediaRecorder output. Fixed phrases (urgent tiles + Wait) live in `data/phrases.json`.

## Environment (`.env`, git-ignored; commit `.env.example` with empty values)

```
GNANI_API_KEY=
OPENAI_API_KEY=
LLM_MODEL=gpt-5.4-mini
OPENAI_BASE_URL=https://api.openai.com/v1
```

## API

| Endpoint | Input | Output |
|---|---|---|
| `GET /api/symbols` | (none) | Tiles from the CSV, labels in the active language |
| `POST /api/listen` | Audio blob (≤30 s, wav/webm), `lang` | `{transcript, language}` via Prisma |
| `POST /api/suggest` | `{history: [last 6 partner lines], tiles: [ids], typed: "", lang}` | `{quick_replies: [tile ids or short labels, max 5], sentences: [max 3], clarify: null or {question, options: [2–3]}}` |
| `POST /api/speak` | `{text, lang}` | WAV from Timbre (cache by text+lang+voice) |

### `/api/suggest` behaviour

- If there are **no tiles and no typed text** and a partner line arrived, return only `quick_replies` that directly answer that line (e.g. "Dal chawal ya khichdi?" → khichdi, dal_chawal, not_hungry, later).
- If there are tiles, return up to 3 short first-person sentences **in the user's language** (Hinglish if the partner spoke Hinglish), using only what the tiles and history support.
- If the tiles could reasonably mean two different things, return `clarify` and **no sentences**.
- The system prompt must say: *never add facts, feelings or requests the user did not select; prefer short sentences; ask when unsure.*
- Respond with strict JSON and validate it server-side. On any LLM error, fall back to concatenating the tile labels so the user can still speak.

## Frontend screens (one page, large touch targets ≥ 72 px)

1. **Top bar:** language picker (Hinglish / Hindi / English / Kannada / Marathi), mode toggle (**Listen** / **Phone**), a live "Partner said: …" line.
2. **Suggested replies row:** big buttons from `quick_replies`, plus **Something else** and **Wait**.
3. **Board:** category tabs + emoji tiles from the CSV. Selected tiles show as chips in a message bar.
4. **Message bar:** chips, a small text box, then **Suggest** → up to 3 sentence cards, or a clarifying question with big Yes/No/option buttons.
5. **Speak** button on each sentence card. Urgent tiles are pinned in a red corner.

### Listening

- Mic capture with `MediaRecorder`. Simple voice-activity detection: start a segment when RMS goes above a threshold, end it after about 1.2 s of silence or 25 s maximum, then send it to `/api/listen`, then automatically call `/api/suggest` with no tiles.
- **Half-duplex:** pause the mic while our own TTS is playing, and for 300 ms after, so phone mode doesn't hear itself.
- Show a small "listening / thinking / ready" status.
- Target: partner stops talking → reply row updated in **under 3 s**. Log timings to the console.

### Phone mode

Same as Listen mode, with a hint banner: "Put the call on speaker next to the tablet". Uses the same half-duplex rule.

## Running on a tablet

Browsers only allow the mic on `https` or `localhost`. Run `uvicorn app.main:app --port 8000` and expose it with `ngrok http 8000`, then open the https URL on the tablet or phone.

## Build order (stop and demo after each step)

1. Skeleton: FastAPI serves the page, `/api/symbols`, the board renders, tiles make chips.
2. `/api/speak` with caching, Speak buttons, urgent tiles.
3. `/api/suggest` with sentences and clarify, wired to the message bar.
4. `/api/listen` + VAD + auto quick replies (Listen mode).
5. Phone mode banner + half-duplex check.
6. Tests: unit tests for suggest JSON validation and the fallback, plus urgent-tile behaviour (mock all network calls).
7. README: what it is, how to run it, Gnani models used, limits.

## Demo scenarios

See `docs/content-pack.md`: "Lunch at home" (Hinglish) and "Calling the chemist" (Kannada/Marathi on speakerphone), plus 15 tricky test lines.
