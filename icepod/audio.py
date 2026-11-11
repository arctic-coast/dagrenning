"""Turn script segments into one MP3.

Two voice providers, chosen with ICEPOD_TTS in .env:

- "microsoft" (default): Gunnar (is-IS-GunnarNeural) for Icelandic and Sonia
  (en-GB-SoniaNeural) for English. Uses the official Azure Speech API when
  AZURE_SPEECH_KEY and AZURE_SPEECH_REGION are set, otherwise the free
  edge-tts route to the same voices.
- "elevenlabs": one ElevenLabs voice for both languages (ELEVENLABS_VOICE_ID,
  default Sarah) on ELEVENLABS_MODEL (default eleven_v4).

Every clip from one provider comes back as MP3 in the same format, so clips
are joined byte-for-byte with silence encoded in that same format (no ffmpeg).

render() also returns timings: where each segment starts and ends in the final
MP3 and, on the edge-tts and ElevenLabs routes, when each word is spoken (for
the read-along player on the episode page). The Azure REST route gives
segment-level timings only.
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

GAP = 0.25                   # seconds between segments
LANG_SWITCH_GAP = 0.4        # a little longer when the language changes
# No repeat-after-me pauses: segments flagged pause_after get the normal gap only.


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


async def _edge_async(text: str, voice: str, rate: str) -> tuple[bytes, list[dict]]:
    """Audio plus word timings (seconds from the start of the clip)."""
    import edge_tts
    buf, words = bytearray(), []
    async for chunk in edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary").stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            start = chunk["offset"] / 1e7  # 100-nanosecond units
            words.append({"text": chunk["text"], "start": start, "end": start + chunk["duration"] / 1e7})
    return bytes(buf), words


def _elevenlabs(text: str, lang: str, slow: bool, before: str = "", after: str = "") -> tuple[bytes, list[dict]]:
    """Audio plus word timings, from ElevenLabs' with-timestamps endpoint.

    `before`/`after` are the neighbouring lines (sent as previous_text/next_text, not spoken)
    so each clip's intonation fits the lines around it.
    """
    import base64
    voice = os.getenv("ELEVENLABS_VOICE_ID") or EL_DEFAULT_VOICE
    model = os.getenv("ELEVENLABS_MODEL") or "eleven_v4"
    body = {"text": text, "model_id": model, "voice_settings": {"speed": EL_SPEED[slow]}}
    if before:
        body["previous_text"] = before
    if after:
        body["next_text"] = after
    if model in ("eleven_flash_v2_5", "eleven_turbo_v2_5"):
        body["language_code"] = lang
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps?output_format=mp3_44100_64",
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json"},
        json=body, timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs HTTP {r.status_code}: {r.text[:200]}")
    d = r.json()
    a = d.get("alignment") or {}
    chars = a.get("characters") or []
    starts = a.get("character_start_times_seconds") or []
    ends = a.get("character_end_times_seconds") or []
    # Group characters into words (split on whitespace) to match the edge-tts word timings.
    words, cur = [], None
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append(cur)
            cur = None
        elif cur is None:
            cur = {"text": ch, "start": s, "end": e}
        else:
            cur["text"] += ch
            cur["end"] = e
    if cur:
        words.append(cur)
    for w in words:  # trim punctuation so the page can find the word in the text
        w["text"] = w["text"].strip(".,!?;:\"'„“”«»()")
    return base64.b64decode(d["audio_base64"]), [w for w in words if w["text"]]


def strip_id3(data: bytes) -> bytes:
    """Drop a leading ID3v2 tag: tags in the middle of a joined file can make players stutter."""
    while data[:3] == b"ID3" and len(data) > 10:
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        data = data[10 + size + (10 if data[5] & 0x10 else 0):]
    return data


# MPEG audio frame header tables (Layer III only, which is all we produce).
_BITRATES = {1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
             2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]}
_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def mp3_seconds(data: bytes) -> float:
    """Playing time of an MP3 clip, counted frame by frame (what a player plays when clips are joined)."""
    pos, seconds = 0, 0.0
    while pos + 4 <= len(data):
        h = int.from_bytes(data[pos:pos + 4], "big")
        version, layer = (h >> 19) & 3, (h >> 17) & 3
        br_i, sr_i, pad = (h >> 12) & 15, (h >> 10) & 3, (h >> 9) & 1
        if (h >> 21) != 0x7FF or version == 1 or layer != 1 or br_i in (0, 15) or sr_i == 3:
            pos += 1  # not a frame header: resync
            continue
        rate = _RATES[version][sr_i]
        kbps = _BITRATES[1 if version == 3 else 2][br_i]
        per_frame = 1152 if version == 3 else 576
        seconds += per_frame / rate
        pos += per_frame // 8 * kbps * 1000 // rate + pad
    return seconds


def speak_timed(text: str, lang: str, slow: bool, before: str = "", after: str = "") -> tuple[bytes, list[dict]]:
    """One clip of speech plus its word timings (empty when the route has none)."""
    p = provider()
    for attempt in range(3):
        try:
            words = []
            if p == "elevenlabs":
                data, words = _elevenlabs(text, lang, slow, before, after)
            elif os.getenv("AZURE_SPEECH_KEY") and os.getenv("AZURE_SPEECH_REGION"):
                data = _azure(text, MS_VOICES[lang], MS_RATES[(lang, slow)])
            else:
                data, words = asyncio.run(_edge_async(text, MS_VOICES[lang], MS_RATES[(lang, slow)]))
            data = strip_id3(data)
            if data:
                return data, words
        except Exception:
            if attempt == 2:
                raise
    raise RuntimeError(f"No audio returned for: {text[:60]}")


def speak(text: str, lang: str, slow: bool) -> bytes:
    return speak_timed(text, lang, slow)[0]


def _place_words(text: str, words: list[dict], shift: float) -> list[dict]:
    """Shift word times into the episode and find each word in the text, so the page can highlight it.
    "at" is the word's character position in the segment text, or None if it couldn't be matched."""
    low, pos, out = text.lower(), 0, []
    for w in words:
        at = low.find(w["text"].lower(), pos) if w["text"] else -1
        if at >= 0:
            pos = at + len(w["text"])
        out.append({"text": w["text"], "at": at if at >= 0 else None,
                    "start": round(w["start"] + shift, 3), "end": round(w["end"] + shift, 3)})
    return out


def render(segments, progress=print) -> tuple[bytes, dict]:
    """segments: objects with .lang, .slow, .pause_after, .text (and optionally .translation).

    Returns the MP3 and its timings: {"duration": s, "segments": [{index, lang, text,
    translation, start, end, words: [{text, at, start, end}]}]}, all in seconds.
    """
    out = bytearray()
    gap, switch = silence(GAP), silence(LANG_SWITCH_GAP)
    t_gap, t_switch = mp3_seconds(gap), mp3_seconds(switch)
    now, timed = 0.0, []
    prev_lang = None
    segs = [s for s in segments if s.text.strip()]
    for i, seg in enumerate(segs):
        text = seg.text.strip()
        if prev_lang is not None:
            out += switch if seg.lang != prev_lang else gap
            now += t_switch if seg.lang != prev_lang else t_gap
        progress(f"  [{i + 1}/{len(segs)}] {seg.lang}{' slow' if seg.slow else ''}: {text[:60]}")
        # Neighbouring lines in the same language, so ElevenLabs can make the joins sound natural.
        before = segs[i - 1].text.strip() if i > 0 and segs[i - 1].lang == seg.lang else ""
        after = segs[i + 1].text.strip() if i + 1 < len(segs) and segs[i + 1].lang == seg.lang else ""
        clip, words = speak_timed(text, seg.lang, seg.slow, before, after)
        out += clip
        length = mp3_seconds(clip)
        timed.append({"index": i, "lang": seg.lang, "text": text,
                      "translation": (getattr(seg, "translation", "") or "").strip(),
                      "start": round(now, 3), "end": round(now + length, 3),
                      "words": _place_words(text, words, now)})
        now += length
        prev_lang = seg.lang
    tail = silence(1.0)
    out += tail
    now += mp3_seconds(tail)
    return bytes(out), {"duration": round(now, 3), "segments": timed}
