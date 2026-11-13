# BRIDGEWORDS

**A listening communication aid for people who can't speak clearly but can tap.**
Built on Gnani **Prisma** (speech-to-text) and Gnani **Timbre** (text-to-speech) for Gnani AI's Great Indian AI Internship Challenge.

Many people with cerebral palsy, motor neurone disease or aphasia understand everything said to them but can't answer in time. Symbol boards are slow, mostly English-only, and deaf to the conversation around them. BRIDGEWORDS:

1. **Listens** to the conversation partner (Prisma, including Hinglish and speakerphone audio).
2. **Turns the board into replies that fit** what was just said: "Dal chawal ya khichdi?" → **Dal chawal · Khichdi · Not hungry · Later**.
3. **Builds full sentences from a few taps**: Khichdi + Soft food → *"Mujhe khichdi chahiye, thodi soft."*
4. **Asks instead of guessing** when taps are ambiguous: Pain + Tomorrow → *"Pain now, or about tomorrow's doctor visit?"*
5. **Speaks only what the user approves**, in their language and their own voice (Timbre).

> Prototype. All data is synthetic: Aarav is a fictional persona with a fictional address. It has not yet been tested with AAC users.

## Safety rules built in

- **Nothing is spoken without the user tapping Speak.** Suggestions are only proposals.
- **Urgent tiles** (🆘 Help, 😮‍💨 Hard to breathe) speak a fixed phrase immediately, with no AI step. Their audio is fetched when the page loads. If Timbre is down they fall back to the device's voice and show a full-screen red card.
- The AI is told: *never add facts, feelings or requests the user did not select; prefer short sentences; ask when unsure*. Its JSON is validated on the server. On any AI error the user gets their tapped words joined together, so they can always speak.
- Every suggestion row also offers **Something else** and **Wait**, so the user is never boxed in.
- **Half-duplex:** the mic ignores everything while the tablet is speaking, plus 300 ms, so phone mode never hears itself.

## Run it

Needs **Python 3.15** (macOS: `brew install python@3.15`; Windows: python.org installer).

```bash
git clone https://github.com/sid596/BridgeWords.git
cd BridgeWords
python3.15 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then paste your keys into .env
uvicorn app.main:app --port 8000
```

Open **http://localhost:8000** in Chrome. Restart the server after editing `.env`.

| `.env` | What it's for |
|---|---|
| `GNANI_API_KEY` | Prisma (listening) and Timbre (speaking) |
| `OPENAI_API_KEY` | Sentence suggestions |
| `LLM_MODEL` | Default `gpt-5.4-mini` |
| `OPENAI_BASE_URL` | Default `https://api.openai.com/v1` |
| `LLM_REASONING_EFFORT` | Optional, default `none` (fastest) |

`.env` is git-ignored. Never commit keys.

### On a tablet or phone

Browsers only allow the mic on `https` or `localhost`. Run `ngrok http 8000` in a second terminal and open the `https://…` link on the tablet.

## Using it

- **Board:** tap tiles. They appear as chips in the message bar. Then tap **Suggest** for up to 3 sentences, and **🔊 Speak** to say one.
- **Listen mode:** tap **🎙 Start listening**. When the partner stops talking, the top row updates with replies to what they said. The status pill shows *listening → hearing → thinking → ready*.
- **Phone mode:** the same, for a call on speaker next to the tablet. The mic is a bit more sensitive in this mode.
- **Language:** Hinglish, हिन्दी, English, ಕನ್ನಡ, मराठी. This changes the tile labels, the suggestions, the listening languages and the voice.
- **Console** (Cmd+Option+J): timings for every step, e.g. `[listen] … stt 820 ms | suggest 1300 ms | total 2120 ms ✓`.

## Editing the content (no code needed)

| File | What's in it |
|---|---|
| `data/symbols.csv` | Tiles: `id, category, label_en, label_hi, label_kn, label_mr, emoji, notes` |
| `data/phrases.json` | Fixed phrases for Help, Hard to breathe and Wait, per language |
| `data/profile.json` | The user's name, gender (for grammar), default language, Timbre model and voice, saved address, personal meanings (e.g. "the heart sometimes means pain") |

- Change labels freely, but **never change an `id`**.
- In Excel, save as **CSV UTF-8**. Plain CSV breaks Kannada and Devanagari text.
- Refresh the browser to see your edits. You don't need to restart the server.

## Gnani models used

| Model | Use | Details |
|---|---|---|
| **Prisma v3.0** (`POST /stt/v3`) | Transcribe the partner | Language lists like `hi-IN,en-IN` / `kn-IN,en-IN`, `format=codemix` keeps English words in Latin script. The browser records raw audio and sends 16 kHz mono WAV, because Prisma does not accept WebM. |
| **Timbre v3.0** (`POST /api/v1/tts/inference`) | Speak approved replies | One voice (default **Devansh**) in every language, so the user keeps *their* voice when switching languages. Hinglish uses `hi-en`. Audio is cached on disk by text + language + model + voice, so each phrase is paid for once. |

The LLM is OpenAI Chat Completions (`gpt-5.4-mini`) with a strict JSON schema.

## How it fits together

```
partner speaks ──► mic + voice detection (browser) ──► /api/listen ──► Prisma
                                                           │ transcript
                                                           ▼
                        reply row ◄── /api/suggest (no tiles) ──► LLM
user taps tiles ──► Suggest ──► /api/suggest ──► sentences or a clarifying question
user taps Speak ──► /api/speak ──► Timbre (cached) ──► speaker   (mic paused meanwhile)
urgent tile ──────────────────────► pre-fetched audio ─► speaker   (no AI)
```

Code: `app/main.py` (API), `app/llm.py` (prompt + validation), `app/gnani.py` (Prisma/Timbre adapters, no automatic retries), `static/` (one page, vanilla JS, no build step).

## Tests

```bash
python -m pytest -q
```

46 tests, all offline: Gnani and OpenAI are mocked, and a guard fails any test that tries to reach the network. They cover suggestion JSON validation, the fallback for every kind of AI failure, urgent tiles (fixed phrase, no AI, cached), TTS caching and errors, and the listening endpoint.

## Measured latency (Mac, home Wi-Fi, warm)

| Step | Typical |
|---|---|
| Prisma transcription (3 s clip) | ~0.8 s |
| LLM quick replies | ~1.2–1.5 s |
| Partner stops → reply row | **~2.1–2.4 s**, plus the 1.2 s of silence the app waits for before deciding the partner has finished |
| Timbre, new phrase / cached | ~1.9 s / instant |

## Limits

- Prototype, not a medical device. It has not been tested with AAC users or speech therapists.
- Needs internet for listening, suggestions and new phrases. Urgent tiles keep working from cache or the device voice.
- Voice detection is simple (a volume threshold). Loud background noise, TV or two people talking at once can trigger it or cut it short.
- Half-duplex means the app can't hear the partner while it's speaking.
- Kannada and Marathi phrases were drafted with AI help and need a native-speaker check.
- One user profile, no accounts, no database. Everything stays in local files.
