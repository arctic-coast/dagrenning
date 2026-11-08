"""Turn script segments into one MP3.

Icelandic segments: Microsoft "Gunnar" (is-IS-GunnarNeural).
English segments: a British English neural voice.

Backend: the official Azure Speech API when AZURE_SPEECH_KEY and
AZURE_SPEECH_REGION are set, otherwise the free edge-tts route to the same
voices. Both return MP3 in the same format, so clips can be joined
byte-for-byte with generated silence in between (no ffmpeg needed).
"""

from __future__ import annotations

import asyncio
import os
from xml.sax.saxutils import escape

import lameenc
import requests

VOICES = {"is": "is-IS-GunnarNeural", "en": "en-GB-SoniaNeural"}
RATES = {("is", False): "-12%", ("is", True): "-35%", ("en", False): "+0%", ("en", True): "-15%"}

SAMPLE_RATE = 24000          # matches audio-24khz-48kbitrate-mono-mp3
BITRATE = 48
GAP = 0.35                   # seconds between segments
LANG_SWITCH_GAP = 0.55       # a little longer when the language changes
PAUSE_AFTER = 3.0            # time to repeat the sentence of the day


def silence(seconds: float) -> bytes:
    enc = lameenc.Encoder()
    enc.set_bit_rate(BITRATE)
    enc.set_in_sample_rate(SAMPLE_RATE)
    enc.set_channels(1)
    enc.set_quality(2)
    pcm = b"\x00\x00" * int(SAMPLE_RATE * seconds)
    return enc.encode(pcm) + enc.flush()


def _azure(text: str, voice: str, rate: str) -> bytes:
    key, region = os.environ["AZURE_SPEECH_KEY"], os.environ["AZURE_SPEECH_REGION"]
    lang = voice[:5]
    ssml = (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{lang}">'
        f'<voice name="{voice}"><prosody rate="{rate}">{escape(text)}</prosody></voice></speak>'
    )
    r = requests.post(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        headers={
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
            "User-Agent": "icepod",
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


def speak(text: str, lang: str, slow: bool) -> bytes:
    voice, rate = VOICES[lang], RATES[(lang, slow)]
    use_azure = os.getenv("AZURE_SPEECH_KEY") and os.getenv("AZURE_SPEECH_REGION")
    for attempt in range(3):
        try:
            data = _azure(text, voice, rate) if use_azure else asyncio.run(_edge_async(text, voice, rate))
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
