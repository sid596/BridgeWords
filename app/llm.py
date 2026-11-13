"""Suggestion engine: OpenAI Chat Completions with strict JSON output.

The model only proposes. The user picks, and nothing is spoken until they tap Speak.
Every response is validated here; on any error the caller falls back to plain tile labels.
"""
import json
import logging
import os
import time

import httpx

log = logging.getLogger("bridgewords.llm")

MAX_QUICK_REPLIES = 5
MAX_SENTENCES = 3
MAX_REPLY_CHARS = 40
MAX_SENTENCE_CHARS = 300
ALWAYS_SHOWN = {"something_else", "wait"}  # the UI adds these to every row itself

RESPONSE_SCHEMA = {
    "name": "suggestions",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["quick_replies", "sentences", "clarify"],
        "properties": {
            "quick_replies": {"type": "array", "items": {"type": "string"}},
            "sentences": {"type": "array", "items": {"type": "string"}},
            "clarify": {
                "anyOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["question", "options"],
                        "properties": {
                            "question": {"type": "string"},
                            "options": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                ]
            },
        },
    },
}

SYSTEM_PROMPT = """\
You help {name}, who understands everything but cannot speak clearly, reply in a live conversation.
{name} taps picture tiles. You turn the taps into short replies that {name} approves before anything is spoken.
{name} decides. You never decide for {name}.

CORE RULES
- Never add facts, feelings or requests the user did not select.
- Prefer short sentences.
- Ask when unsure.

LANGUAGE
- Write everything you return (sentences, short labels, clarifying question, options) in {language}.
- If the partner's latest line is code-mixed Hinglish and the user's language is Hindi or Hinglish, reply in Hinglish (Roman script).
- Write as {name}, in the first person. {name} is {gender}: use {gender} first-person grammar where the language marks gender.

WHAT TO RETURN (strict JSON: quick_replies, sentences, clarify)

Case A: no selected tiles and no typed text (the partner just spoke).
- Return only quick_replies: up to 5 options that directly answer the partner's latest line.
- Prefer tile ids from the catalogue below. If no tile fits, use a short label (1-4 words) in {language}.
- Examples: "Dal chawal ya khichdi?" -> ["dal_chawal", "khichdi", "not_hungry", "later"]. "500 mg aa, 650 aa?" -> ["500", "650", "dont_know"].
- If the partner asked a negative question ("Don't you want to go out?"), do NOT use bare "yes"/"no": use explicit labels such as "I want to go" / "I don't want to go".
- If the latest line is only filler or acknowledgement ("haan haan theek hai"), or not addressed to the user, return quick_replies: [].
- Never include "something_else" or "wait"; the app always adds them.
- sentences: [] and clarify: null.

Case B: the user selected tiles and/or typed text.
- Return up to 3 short first-person sentences. EACH sentence must express ALL the selected tiles together
  (khichdi + soft_food -> "Khichdi chahiye, thodi soft." not one sentence per tile).
- The sentences are alternative WORDINGS of the SAME meaning (e.g. plain, polite, very short). Never offer different meanings to choose from.
- Use the partner lines as context: a tile often answers the partner's question (partner asked "dahi bhi doon?", tiles no + water -> "Nahi, dahi nahi. Bas paani de do.").
- Use only what the tiles, typed text and conversation support. Don't add reasons, feelings, quantities, times or people that were not selected.
- Typed text may be an abbreviation. If the context makes a reading likely ("para" at a chemist -> paracetamol), use the expanded word; the user will check it.
- AMBIGUITY: before answering, ask yourself whether the selection could reasonably mean two different things.
  If yes, return clarify and NO sentences. Typical traps:
  * a body/feeling tile + a time tile (pain + tomorrow: pain now, or about tomorrow's doctor visit?)
  * a place + a feeling (hospital + scared: scared of going, or scared of something at the hospital?)
  * a tile with a personal meaning listed below
  * a partner question that the tiles answer in more than one way
  clarify.question: one short question to {name}, in the second person ("Do you mean ...?"), never as {name} speaking.
  clarify.options: 2-3 very short answers that NAME the alternatives ("Pain now" / "About tomorrow"), or "Yes"/"No" in {language} for a plain yes/no question.
- quick_replies: [].

Case C: a clarification answer is given.
- The user already answered your question. Return sentences that follow that answer. clarify must be null.

PERSONAL MEANINGS for {name} (respect them; when one applies and context is unclear, ask):
{personal_meanings}

SAVED INFO (use only when the selection asks for it, e.g. the "home" tile when the partner asked for the address;
never volunteer it otherwise):
- home address: {address}

TILE CATALOGUE (id: English meaning)
{catalogue}
"""


def build_system_prompt(profile: dict, language_desc: str, tiles: list[dict]) -> str:
    meanings = profile.get("personal_meanings") or {}
    return SYSTEM_PROMPT.format(
        name=profile.get("name", "the user"),
        gender=profile.get("gender", "male"),
        language=language_desc,
        personal_meanings="\n".join(f"- {k}: {v}" for k, v in meanings.items()) or "- none",
        address=profile.get("saved_address") or "not saved",
        catalogue="\n".join(f"{t['id']}: {t['label_en']}" for t in tiles),
    )


def build_user_message(history, selected, typed, clarification) -> str:
    return json.dumps(
        {
            "partner_lines_oldest_first": history,
            "selected_tiles": selected,
            "typed_text": typed,
            "clarification": clarification,
        },
        ensure_ascii=False,
    )


def _clean_list(items, limit, max_chars):
    out, seen = [], set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split())
        if not text or len(text) > max_chars or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(text)
        if len(out) == limit:
            break
    return out


def validate(raw: object, *, has_input: bool, clarified: bool) -> dict:
    """Coerce model output into the API contract. Raises ValueError if unusable."""
    if not isinstance(raw, dict):
        raise ValueError("LLM output is not a JSON object")

    quick = [q for q in _clean_list(raw.get("quick_replies"), MAX_QUICK_REPLIES + 2, MAX_REPLY_CHARS)
             if q not in ALWAYS_SHOWN][:MAX_QUICK_REPLIES]
    sentences = _clean_list(raw.get("sentences"), MAX_SENTENCES, MAX_SENTENCE_CHARS)

    clarify = None
    c = raw.get("clarify")
    if isinstance(c, dict) and isinstance(c.get("question"), str) and c["question"].strip():
        options = _clean_list(c.get("options"), 3, MAX_REPLY_CHARS)
        if len(options) >= 2:
            clarify = {"question": " ".join(c["question"].split()), "options": options}

    if not has_input:
        # Partner just spoke: only quick replies.
        return {"quick_replies": quick, "sentences": [], "clarify": None}

    if clarified:
        clarify = None  # never ask twice in a row
    if clarify:
        return {"quick_replies": [], "sentences": [], "clarify": clarify}
    if not sentences:
        raise ValueError("LLM returned neither sentences nor a usable clarifying question")
    return {"quick_replies": [], "sentences": sentences, "clarify": None}


async def suggest(*, profile, language_desc, catalogue, history, selected, typed, clarification) -> dict:
    """Call the LLM and return validated suggestions. Raises on any failure."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY is not set")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "gpt-5.4-mini")

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": build_system_prompt(profile, language_desc, catalogue)},
            {"role": "user", "content": build_user_message(history, selected, typed, clarification)},
        ],
        "response_format": {"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
        "max_completion_tokens": 1500,
    }
    effort = os.environ.get("LLM_REASONING_EFFORT", "none").strip()
    if effort:
        body["reasoning_effort"] = effort

    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as client:
        response = await client.post(f"{base}/chat/completions", json=body,
                                     headers={"Authorization": f"Bearer {key}"})
    if response.status_code != 200:
        # Log OpenAI's error message (never contains our key), not the request.
        try:
            detail = response.json().get("error", {}).get("message", "")[:200]
        except ValueError:
            detail = ""
        raise ValueError(f"LLM HTTP {response.status_code}: {detail}")

    message = response.json()["choices"][0]["message"]
    if message.get("refusal"):
        raise ValueError("LLM refused")
    raw = json.loads(message.get("content") or "")
    result = validate(raw, has_input=bool(selected or typed), clarified=bool(clarification))
    log.info("LLM ok model=%s took=%.0fms", model, (time.perf_counter() - t0) * 1000)
    return result
