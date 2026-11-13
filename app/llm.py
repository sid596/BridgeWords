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
        "required": ["quick_replies", "sentences", "spoken", "clarify", "translation"],
        "properties": {
            "quick_replies": {"type": "array", "items": {"type": "string"}},
            "sentences": {"type": "array", "items": {"type": "string"}},
            "spoken": {"type": "array", "items": {"type": "string"}},
            "translation": {"type": "string"},
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
- Write sentences, short labels, the clarifying question and its options in {language}. Only "spoken" is in the partner's language.
- If the partner's latest line is code-mixed Hinglish and the user's language is Hindi or Hinglish, reply in Hinglish (Roman script).
- Write as {name}, in the first person. {name} is {gender}: use {gender} first-person grammar where the language marks gender.

{partner_section}
WHAT TO RETURN (strict JSON: quick_replies, sentences, spoken, clarify, translation)

Case A: no selected tiles and no typed text (the partner just spoke).
- Return only quick_replies: up to 5 options that directly answer the partner's latest line.
  Fewer is better: 2-4 good options beat 5 with filler. Never pad with unrelated tiles.
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
  Every sentence (including the 2nd and 3rd) must stay inside the selection. Forbidden example: tiles medicine + "para"
  -> "I need medicine for pain" (pain was never selected). Allowed: "I need paracetamol." / "Paracetamol, please."
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


PARTNER_SECTION = """\
PARTNER'S LANGUAGE
- The partner speaks {partner}. {name} reads and taps in {language}.
{spoken_rule}
{translation_rule}
"""
SPOKEN_RULE = ("- spoken: for EVERY sentence, the same sentence in {partner}, in the same order. This is what is said aloud "
               "to the partner, so it must be natural and polite in {partner} and mean exactly the same, adding nothing.")
NO_SPOKEN_RULE = "- spoken: [] (replies are spoken in {language})."
TRANSLATION_RULE = ("- translation: REQUIRED. Translate the text in \"translate_this\" (what the partner just said) "
                    "faithfully into {language}, so {name} understands it. Translate the partner's words, never {name}'s "
                    "reply. Translate the MEANING into real {language} words; never just write the partner's words in another "
                    "script (\"Heli sir, enu beku?\" is NOT a translation; \"Boliye sir, kya chahiye?\" is). "
                    "Keep names and numbers.")
NO_TRANSLATION_RULE = "- translation: \"\" ({name} understands the partner directly)."
SAME_LANGUAGE_SECTION = """\
LANGUAGE OF THE CONVERSATION
- spoken: [] and translation: "" (the partner shares {name}'s language).
"""


def build_partner_section(profile, language_desc, partner_desc, want_spoken, want_translation) -> str:
    name = profile.get("name", "the user")
    if not partner_desc:
        return SAME_LANGUAGE_SECTION.format(name=name)
    fmt = {"name": name, "partner": partner_desc, "language": language_desc}
    return PARTNER_SECTION.format(
        **fmt,
        spoken_rule=(SPOKEN_RULE if want_spoken else NO_SPOKEN_RULE).format(**fmt),
        translation_rule=(TRANSLATION_RULE if want_translation else NO_TRANSLATION_RULE).format(**fmt),
    )


def build_system_prompt(profile: dict, language_desc: str, tiles: list[dict], partner_desc: str | None = None,
                        want_spoken: bool = False, want_translation: bool = False) -> str:
    meanings = profile.get("personal_meanings") or {}
    return SYSTEM_PROMPT.format(
        partner_section=build_partner_section(profile, language_desc, partner_desc, want_spoken, want_translation),
        name=profile.get("name", "the user"),
        gender=profile.get("gender", "male"),
        language=language_desc,
        personal_meanings="\n".join(f"- {k}: {v}" for k, v in meanings.items()) or "- none",
        address=profile.get("saved_address") or "not saved",
        catalogue="\n".join(f"{t['id']}: {t['label_en']}" for t in tiles),
    )


def build_user_message(history, selected, typed, clarification, translate_this=None) -> str:
    msg = {
        "partner_lines_oldest_first": history,
        "selected_tiles": selected,
        "typed_text": typed,
        "clarification": clarification,
    }
    if translate_this:
        msg["translate_this"] = translate_this
    return json.dumps(msg, ensure_ascii=False)


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


def _clean_pairs(sentences, spoken):
    """Sentences with their spoken (partner-language) versions, kept in step."""
    if not isinstance(sentences, list) or not isinstance(spoken, list) or len(sentences) != len(spoken):
        raise ValueError("spoken must have one entry per sentence")
    out, seen = [], set()
    for a, b in zip(sentences, spoken):
        if not isinstance(a, str) or not isinstance(b, str):
            continue
        a, b = " ".join(a.split()), " ".join(b.split())
        if not a or not b or len(a) > MAX_SENTENCE_CHARS or len(b) > MAX_SENTENCE_CHARS or a.lower() in seen:
            continue
        seen.add(a.lower())
        out.append((a, b))
        if len(out) == MAX_SENTENCES:
            break
    return [a for a, _ in out], [b for _, b in out]


def validate(raw: object, *, has_input: bool, clarified: bool,
             want_spoken: bool = False, want_translation: bool = False) -> dict:
    """Coerce model output into the API contract. Raises ValueError if unusable."""
    if not isinstance(raw, dict):
        raise ValueError("LLM output is not a JSON object")

    quick = [q for q in _clean_list(raw.get("quick_replies"), MAX_QUICK_REPLIES + 2, MAX_REPLY_CHARS)
             if q not in ALWAYS_SHOWN][:MAX_QUICK_REPLIES]
    translation = ""
    if want_translation and isinstance(raw.get("translation"), str):
        translation = " ".join(raw["translation"].split())[:MAX_SENTENCE_CHARS]
    if want_spoken and has_input and raw.get("sentences"):
        sentences, spoken = _clean_pairs(raw.get("sentences"), raw.get("spoken"))
    else:
        sentences, spoken = _clean_list(raw.get("sentences"), MAX_SENTENCES, MAX_SENTENCE_CHARS), []

    clarify = None
    c = raw.get("clarify")
    if isinstance(c, dict) and isinstance(c.get("question"), str) and c["question"].strip():
        options = _clean_list(c.get("options"), 3, MAX_REPLY_CHARS)
        if len(options) >= 2:
            clarify = {"question": " ".join(c["question"].split()), "options": options}

    out = {"quick_replies": [], "sentences": [], "spoken": [], "clarify": None, "translation": translation}
    if not has_input:
        # Partner just spoke: only quick replies (+ a translation of what they said).
        return {**out, "quick_replies": quick}

    if clarified:
        clarify = None  # never ask twice in a row
    if clarify:
        return {**out, "clarify": clarify}
    if not sentences:
        raise ValueError("LLM returned neither sentences nor a usable clarifying question")
    return {**out, "sentences": sentences, "spoken": spoken}


_client: httpx.AsyncClient | None = None


async def post_chat(url: str, body: dict, key: str) -> tuple[int, object]:
    """The only network call in this module (tests replace it).
    One shared client keeps the HTTPS connection warm, saving a TLS handshake per suggestion."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0))
    response = await _client.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, None


async def suggest(*, profile, language_desc, catalogue, history, selected, typed, clarification,
                  partner_desc=None, want_spoken=False, want_translation=False) -> dict:
    """Call the LLM and return validated suggestions. Raises on any failure."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY is not set")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "gpt-5.4-mini")

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": build_system_prompt(profile, language_desc, catalogue, partner_desc,
                                                              want_spoken, want_translation)},
            {"role": "user", "content": build_user_message(history, selected, typed, clarification,
                                                           history[-1] if want_translation and history else None)},
        ],
        "response_format": {"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
        "max_completion_tokens": 1500,
    }
    effort = os.environ.get("LLM_REASONING_EFFORT", "none").strip()
    if effort:
        body["reasoning_effort"] = effort

    t0 = time.perf_counter()
    status, payload = await post_chat(f"{base}/chat/completions", body, key)
    if status != 200:
        # Log OpenAI's error message (never contains our key), not the request.
        detail = (payload.get("error") or {}).get("message", "")[:200] if isinstance(payload, dict) else ""
        raise ValueError(f"LLM HTTP {status}: {detail}")

    message = payload["choices"][0]["message"]
    if message.get("refusal"):
        raise ValueError("LLM refused")
    raw = json.loads(message.get("content") or "")
    result = validate(raw, has_input=bool(selected or typed), clarified=bool(clarification),
                      want_spoken=want_spoken, want_translation=want_translation)
    log.info("LLM ok model=%s took=%.0fms", model, (time.perf_counter() - t0) * 1000)
    return result
