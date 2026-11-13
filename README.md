# BridgeWords

Listening communication aid for people with speech disabilities, built on Gnani Prisma + Timbre.

> Work in progress for the Gnani AI Great Indian AI Internship Challenge. See `CLAUDE.md` for the full spec.
> Current state: **step 1**: the symbol board, category tabs, chips and language picker.

## Run it locally

Needs **Python 3.15** (macOS: `brew install python@3.15`; Windows: python.org installer).

```bash
git clone https://github.com/sid596/BridgeWords.git
cd BridgeWords

python3.15 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # Windows: copy .env.example .env
# Fill in the keys in .env. They aren't needed until step 2, and must never be committed.

uvicorn app.main:app --port 8000
```

Open http://localhost:8000.

**VS Code:** if it shows `Import "httpx" could not be resolved`, run *Python: Select Interpreter* and pick `.venv`.

## Editing the tiles

All tile words live in `data/symbols.csv`, with one row per tile and labels in English, Hindi, Kannada and Marathi.

- Change the words freely, but never change the `id` column.
- In Excel, save as **CSV UTF-8**. Plain CSV breaks Kannada and Devanagari text.
- Refresh the browser to see your changes. You don't need to restart the server.

All data in this repo is synthetic (fictional persona, fictional address).
