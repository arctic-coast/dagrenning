"""Turn script segments into one MP3.

Two voice providers, chosen with ICEPOD_TTS in .env:

- "microsoft" (default): Gunnar (is-IS-GunnarNeural) for Icelandic and Sonia
  (en-GB-SoniaNeural) for English. Uses the official Azure Speech API when
  AZURE_SPEECH_KEY and AZURE_SPEECH_REGION are set, otherwise the free
  edge-tts route to the same voices.
- "elevenlabs": one ElevenLabs voice for both languages (ELEVENLABS_VOICE_ID,
  default Sarah) on ELEVENLABS_MODEL (default eleven_v3).

Every clip from one provider comes back as MP3 in the same format, so clips
are joined byte-for-byte with silence encoded in that same format (no ffmpeg).
"""

from __future__ import annotations

import asyncio
import os
from xml.sax.saxutils import escape

import lameenc
import requests

MS_VOICES = {"is": "is-IS-GunnarNeural", "en": "en-GB-SoniaNeural"}
MS_RATES = {("is", False): "-12%", ("is", True): "-35%", ("en", False): "+0%", ("en", True): "-15%"}
EL_SPEED = {False: 0.95, True: 0.8}
EL_DEFAULT_VOICE = "EXAVITQu4vr4xnSDxMaL"  # Sarah

# MP3 format per provider: (sample rate, kbps). Silence must match the speech.
FORMATS = {"microsoft": (24000, 48), "elevenlabs": (44100, 64)}

GAP = 0.35                   # seconds between segments
LANG_SWITCH_GAP = 0.55       # a little longer when the language changes
PAUSE_AFTER = 3.0            # time to repeat the sentence of the day


def provider() -> str:
    p = os.getenv("ICEPOD_TTS", "microsoft").strip().lower()
    if p not in FORMATS:
        raise ValueError(f"ICEPOD_TTS must be one of {list(FORMATS)}, not {p!r}")
    return p


def bitrate() -> int:
    return FORMATS[provider()][1]


def silence(seconds: float) -> bytes:
    rate, kbps = FORMATS[provider()]
    enc = lameenc.Encoder()
    enc.set_bit_rate(kbps)
    enc.set_in_sample_rate(rate)
    enc.set_channels(1)
    enc.set_quality(2)
    pcm = b"\x00\x00" * int(rate * seconds)
    return enc.encode(pcm) + enc.flush()


def _azure(text: str, voice: str, rate: str) -> bytes:
    key, region = os.environ["AZURE_SPEECH_KEY"], os.environ["AZURE_SPEECH_REGION"]
    ssml = (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{voice[:5]}">'
        f'<voice name="{voice}"><prosody rate="{rate}">{escape(text)}</prosody></voice></speak>'
    )
    r = requests.post(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        headers={
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
            "User-Agent": "dagrenning",
        },
        data=ssml.encode("utf-8"), timeout=60,
    )
    r.raise_for_status()
    return r.content


async def _edge_async(text: str, voice: str, rate: str) -> bytes:
    import edge_tts
    buf = bytearray()
    async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf)


def _elevenlabs(text: str, lang: str, slow: bool) -> bytes:
    voice = os.getenv("ELEVENLABS_VOICE_ID") or EL_DEFAULT_VOICE
    model = os.getenv("ELEVENLABS_MODEL") or "eleven_v3"
    body = {"text": text, "model_id": model, "voice_settings": {"speed": EL_SPEED[slow]}}
    if model in ("eleven_flash_v2_5", "eleven_turbo_v2_5"):
        body["language_code"] = lang
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_64",
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json"},
        json=body, timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs HTTP {r.status_code}: {r.text[:200]}")
    return r.content


def strip_id3(data: bytes) -> bytes:
    """Drop a leading ID3v2 tag: tags in the middle of a joined file can make players stutter."""
    while data[:3] == b"ID3" and len(data) > 10:
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        data = data[10 + size + (10 if data[5] & 0x10 else 0):]
    return data


def speak(text: str, lang: str, slow: bool) -> bytes:
    p = provider()
    for attempt in range(3):
        try:
            if p == "elevenlabs":
                data = _elevenlabs(text, lang, slow)
            elif os.getenv("AZURE_SPEECH_KEY") and os.getenv("AZURE_SPEECH_REGION"):
                data = _azure(text, MS_VOICES[lang], MS_RATES[(lang, slow)])
            else:
                data = asyncio.run(_edge_async(text, MS_VOICES[lang], MS_RATES[(lang, slow)]))
            data = strip_id3(data)
            if data:
                return data
        except Exception:
            if attempt == 2:
                raise
    raise RuntimeError(f"No audio returned for: {text[:60]}")


def render(segments, progress=print) -> bytes:
    """segments: objects with .lang, .slow, .pause_after, .text"""
    out = bytearray()
    gap, switch, pause = silence(GAP), silence(LANG_SWITCH_GAP), silence(PAUSE_AFTER)
    prev_lang = None
    for i, seg in enumerate(segments, 1):
        text = seg.text.strip()
        if not text:
            continue
        if prev_lang is not None:
            out += switch if seg.lang != prev_lang else gap
        progress(f"  [{i}/{len(segments)}] {seg.lang}{' slow' if seg.slow else ''}: {text[:60]}")
        out += speak(text, seg.lang, seg.slow)
        if seg.pause_after:
            out += pause
        prev_lang = seg.lang
    out += silence(1.0)
    return bytes(out)
