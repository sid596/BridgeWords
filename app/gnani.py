"""REST adapters based on Gnani's docs, checked 2026-11-13 (docs.gnani.ai).
No retries: repeated billable requests require an explicit user action.

Prisma v3.0 accepts a comma-separated language list (e.g. "hi-IN,en-IN") or "indic-auto",
and format="codemix" keeps English words in Latin script. WebM is not accepted, so the
browser sends WAV. Timbre v3.0 voices speak all supported languages; "hi-en" is Hinglish.
"""
import os
import httpx

BASE = "https://api.vachana.ai"
STT_MODEL = "prisma-v3.0"
DEFAULT_TTS_MODEL = "timbre-v2.5"


def headers():
    key = os.environ.get("GNANI_API_KEY")
    if not key:
        raise ValueError("Set GNANI_API_KEY locally before making a voice request")
    return {"X-API-Key-ID": key}


def transcribe(audio, filename="answer.wav", language="hi-IN", fmt=None):
    data = {"model": STT_MODEL, "language_code": language}
    if fmt:
        data["format"] = fmt
    response = httpx.post(BASE + "/stt/v3", headers=headers(),
        files={"audio_file": (filename, audio, "audio/wav")},
        data=data, timeout=60)
    response.raise_for_status()
    data = response.json()
    if data.get("success") is not True or not isinstance(data.get("transcript"), str):
        raise ValueError("Prisma response did not contain a successful transcript")
    return data


def synthesize(text, language="hi-IN", voice="Nalini", speed=1.0, model=DEFAULT_TTS_MODEL):
    response = httpx.post(BASE + "/api/v1/tts/inference", headers=headers(),
        json={"text": text, "voice": voice, "model": model, "language": language, "speed": speed,
              "audio_config": {"sample_rate": 48000, "num_channels": 1, "sample_width": 2,
                               "encoding": "linear_pcm", "container": "wav"}}, timeout=60)
    response.raise_for_status()
    if not response.content.startswith(b"RIFF"):
        raise ValueError("Timbre did not return the requested WAV audio")
    return response.content
