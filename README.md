# BRIDGEWORDS

**A listening communication aid for people who can't speak clearly but can tap.**
Built on Gnani **Prisma** (speech-to-text) and Gnani **Timbre** (text-to-speech) for Gnani AI's Great Indian AI Internship Challenge.

Many people with cerebral palsy, motor neurone disease or aphasia understand everything said to them but can't answer in time. Symbol boards are slow, mostly English-only, and deaf to the conversation around them. BRIDGEWORDS:

1. **Listens** to the conversation partner (Prisma, including Hinglish and speakerphone audio).
2. **Turns the board into replies that fit** what was just said: "Dal chawal ya khichdi?" → **Dal chawal · Khichdi · Not hungry · Later**.
3. **Builds full sentences from a few taps**: Khichdi + Soft food → *"Mujhe khichdi chahiye, thodi soft."*
4. **Asks instead of guessing** when taps are ambiguous: Pain + Tomorrow → *"Pain now, or about tomorrow's doctor visit?"*
5. **Speaks only what the user approves**, in their language and their own voice (Timbre).
6. **Bridges languages.** If the partner speaks a language the user doesn't (a Kannada-only chemist, a Hindi- and English-speaking user), it translates what they said and speaks the reply **in the partner's language**. This covers all 10 languages Gnani supports.
7. **Learns the person's life.** The board starts with tiles most people need. Then it proposes tiles that are only theirs: 🪂 a parachute for a skydiver, 🍝 pasta instead of khichdi, 🩼 their crutch.

> Prototype. All data is synthetic: Aarav is a fictional persona with a fictional address. It has not yet been tested with AAC users.

## Safety rules built in

- **Nothing is spoken without the user tapping Speak.** Suggestions are only proposals.
- **Urgent tiles** (🆘 Help, 😮‍💨 Hard to breathe) speak a fixed phrase immediately, with no AI step. Their audio is fetched when the page loads. If Timbre is down they fall back to the device's voice and show a full-screen red card.
- The AI is told: *never add facts, feelings or requests the user did not select; prefer short sentences; ask when unsure*. Its JSON is validated on the server. On any AI error the user gets their tapped words joined together, so they can always speak.
- Every suggestion row also offers **Something else** and **Wait**, so the user is never boxed in.
- **Half-duplex:** the mic ignores everything while the tablet is speaking, plus 300 ms, so phone mode never hears itself.

## Built for people with CP

- **Looks and works like an AAC device.** Tiles use the modified **Fitzgerald Key** colours that speech therapists know: people yellow, actions green, feelings blue, things orange, social words pink, and the user's own tiles purple. The interface uses **Atkinson Hyperlegible**, a typeface the Braille Institute designed for readers with low vision.
- **Tile size**: Normal, Large or Extra large, for less precise hand movement.
- **Ignore repeated taps** (0.4–1.5 s): tremor often causes double presses, so a second tap on the same button within that time is ignored.
- **Switch scanning**: groups light up, then the buttons inside them. One switch (Space or Enter on a switch interface, or a tap anywhere) chooses. Many people with severe CP use AAC this way instead of pointing.
- **Said before**: the last 5 sentences, one tap to repeat.
- **Show**: any sentence full-screen in big text, for noisy places or a partner who is hard of hearing.
- **My voice**: Gnani voice cloning from 5–30 s of a voice the user chooses, for example a family member's. It needs an explicit consent checkbox. Only the voice embedding is kept, in `user_data/`; the recording is never saved.
- **Works without internet where it matters**: it installs to the home screen (Add to Home Screen) and the page and board load offline. Urgent tiles still speak, from cached audio or the device voice, and a banner says when the internet is down.

Every setting is in the settings drawer (top right) and is remembered on the device.

## Languages and translation

- **Board language** (Hinglish, हिन्दी, English, ಕನ್ನಡ, मराठी): everything the user reads changes, including tiles, buttons, tabs, status and messages. Interface text lives in `data/ui.json`.
- **Partner speaks** (top bar): *Same as me*, *Auto-detect* (Prisma `indic-auto`), or any of the 10 Gnani languages: Hindi, English, Kannada, Marathi, Tamil, Telugu, Malayalam, Bengali, Gujarati, Punjabi, plus Hinglish.
- When the partner's language is one the user doesn't understand (`understands` in `data/profile.json`):
  - **Partner said:** ಹೇಳಿ ಸರ್, ಏನು ಬೇಕು?  →  **Meaning:** Boliye sir, kya chahiye?
  - The user builds a reply in their own language, and each card shows **🗣 Will say (ಕನ್ನಡ): ನನಗೆ ಪ್ಯಾರಾಸಿಟಮಾಲ್ ಬೇಕು.** Speak says the Kannada version.
  - Urgent tiles and Wait speak the partner's language when the phrase exists in it.
- The translation comes from the same AI call as the suggestions, so it adds no extra wait (about 1.3–1.5 s). If the AI fails, the tile labels are spoken in the partner's language where the board has them.

## A board that learns the person

Everybody's life is different, and a fixed board can't know that this user skydives, eats pasta or never goes anywhere without a crutch. BRIDGEWORDS keeps a private log of the conversation: what partners said, what the user typed (the strongest signal, because typing is slow for them), and what they chose to speak. After every 4 things a partner says, it asks the AI: *what does this person talk about often that the board lacks?*

- **The AI proposes, the user decides.** Ideas appear as "✨ New tile ideas: 🪂 Parachute [+ Add] [✕]". Nothing is added by itself, which is the same principle as Speak.
- **No invented interests.** Each idea must quote at least 2 real lines from the log, or something the user typed. The server checks that the quotes exist, and drops anything already on the board, rejected before, or private (phone numbers, addresses, money).
- **Tiles never move on their own.** AAC users rely on motor memory, so new tiles go into a **My tiles** tab. **✏️ Edit** lets the user hide starter tiles they never use (khichdi) and remove their own. Urgent tiles can't be hidden.
- **Private and local.** The log and learned tiles live in `user_data/`, which is git-ignored and never committed. **🧹 Forget conversations** wipes the log. Set `"learning": false` in `data/profile.json` to turn learning off.

Tested on a synthetic week of conversation: it proposed 🪂 Parachute, 🩼 Crutch (बैसाखी) and 🍝 Pasta, and ignored a phone number, an address and a one-off zoo trip.

## Demo mode

Open `/?demo=1` for a strip where you type what the partner said (or tap a scripted line from both scenarios), plus **Load sample week**, which loads a fictional week of conversation so the board-learning ideas (🪂 Parachute, 🩼 Crutch, 🍝 Pasta) appear on cue. It's useful for rehearsing, recording the video, and judges trying it without a second person.

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
- **Timings:** tap the status pill (or open `/?debug=1`) for an on-screen strip with the mic level, the speech threshold, what Prisma heard, and the time for each step. It works on a tablet without dev tools. The console (Cmd+Option+J) has the same details.

## Editing the content (no code needed)

| File | What's in it |
|---|---|
| `data/symbols.csv` | Starter tiles: `id, category, label_en, label_hinglish, label_hi, label_kn, label_mr, emoji, notes` |
| `data/phrases.json` | Fixed phrases for Help, Hard to breathe and Wait, per language |
| `data/profile.json` | The user's name, gender (for grammar), default language, learning on/off, Timbre model and voice, saved address, personal meanings (e.g. "the heart sometimes means pain") |

- Change labels freely, but **never change an `id`**.
- In Excel, save as **CSV UTF-8**. Plain CSV breaks Kannada and Devanagari text.
- Refresh the browser to see your edits. You don't need to restart the server.

## Gnani models used

| Model | Use | Details |
|---|---|---|
| **Prisma v3.0** (`POST /stt/v3`) | Transcribe the partner | Language lists like `hi-IN,en-IN` / `kn-IN,en-IN`, `format=codemix` keeps English words in Latin script. The browser records raw audio and sends 16 kHz mono WAV, because Prisma does not accept WebM. |
| **Voice cloning** (`/api/v1/tts/voice-clone/embeddings`, then TTS with `vachana-vc-v1`) | The user's own voice | Optional, in Settings → My voice. Consent required. Only the embedding is stored. |
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

Code: `app/main.py` (API), `app/llm.py` (prompt + validation), `app/learning.py` (personal board learning), `app/gnani.py` (Prisma/Timbre adapters, no automatic retries), `static/` (one page, vanilla JS, no build step).

## Tests

```bash
python -m pytest -q
```

102 tests, all offline: Gnani and OpenAI are mocked, and a guard fails any test that tries to reach the network. They cover suggestion JSON validation, the fallback for every kind of AI failure, urgent tiles (fixed phrase, no AI, cached), TTS caching and errors, the listening endpoint, board learning (proposals never auto-add, evidence checks, reject/hide/forget, privacy switch), and languages (translation only when needed, replies spoken in the partner's language, all 10 Gnani languages, complete interface text in every board language).

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
- The Gnani account allows one active request at a time, so the server queues Gnani calls. That's also why listening uses short recordings rather than streaming: a stream would hold the only slot and block Speak.
- It isn't documented which languages voice cloning supports; we tested it with Hinglish.
- Kannada and Marathi phrases were drafted with AI help and need a native-speaker check.
- One user profile, no accounts, no database. Everything stays in local files.
