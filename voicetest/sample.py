"""Generate Icelandic voice samples from every provider we can reach.

Usage (from the project folder):
    uv run python voicetest/sample.py              # all providers
    uv run python voicetest/sample.py edge piper   # only some

Providers without the API keys they need are skipped. Keys go in .env
(see .env.example). Output: samples/<provider>/<voice>/<clip>.mp3 plus
samples/manifest.json, which the comparison page reads.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import traceback
import wave
from pathlib import Path
from xml.sax.saxutils import escape

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from texts import CLIPS, ENGLISH_SAMPLE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples"
PIPER_DIR = ROOT / "voices" / "piper"
load_dotenv(ROOT / ".env")

manifest: list[dict] = []


def record(provider, voice, clip_id, path=None, error=None, note=""):
    entry = {
        "provider": provider,
        "voice": voice,
        "clip": clip_id,
        "file": path.relative_to(OUT).as_posix() if path else None,
        "error": error,
        "note": note,
    }
    manifest.append(entry)
    status = "ok " if path else "ERR"
    print(f"  [{status}] {provider:<11} {voice:<34} {clip_id:<9} {error or ''}")


def out_path(provider, voice, clip_id):
    import re
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", voice).strip("_")
    p = OUT / provider / safe / f"{clip_id}.mp3"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def clips_with_english():
    items = [(cid, c["text"], c["slow"]) for cid, c in CLIPS.items()]
    return items


def run(provider, voice, clip_id, fn, note=""):
    path = out_path(provider, voice, clip_id)
    try:
        data = fn()
        path.write_bytes(data)
        record(provider, voice, clip_id, path, note=note)
    except Exception as e:  # keep going: one failure shouldn't stop the run
        msg = str(e).strip().splitlines()[0][:200] if str(e).strip() else type(e).__name__
        record(provider, voice, clip_id, error=msg, note=note)


# ---------------------------------------------------------------- edge-tts
# Free route to Microsoft's neural voices (the same Gudrun/Gunnar as Azure).
def provider_edge():
    import edge_tts

    async def synth(text, voice, rate):
        buf = bytearray()
        async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        if not buf:
            raise RuntimeError("no audio returned")
        return bytes(buf)

    for voice in ["is-IS-GudrunNeural", "is-IS-GunnarNeural"]:
        for cid, text, slow in clips_with_english():
            rate = "-30%" if slow else "-5%"
            run("edge", voice, cid, lambda: asyncio.run(synth(text, voice, rate)))
    run("edge", "en-GB-SoniaNeural", "english",
        lambda: asyncio.run(synth(ENGLISH_SAMPLE, "en-GB-SoniaNeural", "-5%")),
        note="English voice for the explanations")


# ---------------------------------------------------------------- Azure
def provider_azure():
    key, region = os.getenv("AZURE_SPEECH_KEY"), os.getenv("AZURE_SPEECH_REGION")
    if not key or not region:
        print("  skipped: set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION in .env")
        return
    url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3",
        "User-Agent": "icepod-voicetest",
    }

    def synth(text, voice, rate, lang="is-IS", wrap_lang=False):
        body = escape(text)
        if wrap_lang:  # multilingual voices: tell them the text is Icelandic
            body = f'<lang xml:lang="{lang}">{body}</lang>'
        ssml = (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{lang}">'
            f'<voice name="{voice}"><prosody rate="{rate}">{body}</prosody></voice></speak>'
        )
        r = requests.post(url, headers=headers, data=ssml.encode("utf-8"), timeout=60)
        if r.status_code != 200 or not r.content:
            raise RuntimeError(f"HTTP {r.status_code} {r.text[:150]}")
        return r.content

    for voice in ["is-IS-GudrunNeural", "is-IS-GunnarNeural"]:
        for cid, text, slow in clips_with_english():
            rate = "-30%" if slow else "-5%"
            run("azure", voice, cid, lambda: synth(text, voice, rate))
    # Experiment: a multilingual voice told to speak Icelandic. May be unsupported.
    mv = "en-US-AvaMultilingualNeural"
    run("azure", mv, "passage",
        lambda: synth(CLIPS["passage"]["text"], mv, "-5%", wrap_lang=True),
        note="Experiment: English multilingual voice asked to speak Icelandic")


# ---------------------------------------------------------------- ElevenLabs
def provider_elevenlabs():
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        print("  skipped: set ELEVENLABS_API_KEY in .env")
        return
    h = {"xi-api-key": key}
    models = os.getenv("ELEVENLABS_MODELS", "eleven_v3,eleven_multilingual_v2").split(",")

    voice_ids = [v for v in os.getenv("ELEVENLABS_VOICE_IDS", "").split(",") if v]
    voices: list[tuple[str, str]] = []
    r = requests.get("https://api.elevenlabs.io/v1/voices", headers=h, timeout=30)
    r.raise_for_status()
    all_voices = r.json().get("voices", [])
    if voice_ids:
        by_id = {v["voice_id"]: v["name"] for v in all_voices}
        voices = [(vid, by_id.get(vid, vid)) for vid in voice_ids]
    else:  # one female + one male premade voice
        for gender in ["female", "male"]:
            for v in all_voices:
                if v.get("category") == "premade" and v.get("labels", {}).get("gender") == gender:
                    voices.append((v["voice_id"], v["name"]))
                    break

    def synth(text, vid, model, speed):
        body = {"text": text, "model_id": model, "voice_settings": {"speed": speed}}
        if model in ("eleven_flash_v2_5", "eleven_turbo_v2_5"):
            body["language_code"] = "is"
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{vid}?output_format=mp3_44100_128",
            headers={**h, "Content-Type": "application/json"}, json=body, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} {r.text[:150]}")
        return r.content

    for vid, name in voices:
        for model in models:
            label = f"{name} ({model})"
            for cid, text, slow in clips_with_english():
                speed = 0.75 if slow else 0.95
                run("elevenlabs", label, cid, lambda: synth(text, vid, model, speed))


# ---------------------------------------------------------------- Piper
# Free, offline, open source. Voices trained on Icelandic recordings (Talrómur).
def provider_piper():
    import lameenc
    from piper import PiperVoice
    from piper.config import SynthesisConfig

    def to_mp3(wav_bytes):
        with wave.open(io.BytesIO(wav_bytes)) as w:
            rate, ch, frames = w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())
        enc = lameenc.Encoder()
        enc.set_bit_rate(96)
        enc.set_in_sample_rate(rate)
        enc.set_channels(ch)
        enc.set_quality(2)
        return enc.encode(frames) + enc.flush()

    models = sorted(PIPER_DIR.glob("is_IS-*.onnx"))
    if not models:
        print("  skipped: no Piper models in voices/piper")
        return
    for model in models:
        voice = PiperVoice.load(str(model))
        name = model.stem
        for cid, text, slow in clips_with_english():
            cfg = SynthesisConfig(length_scale=1.4 if slow else 1.05)

            def synth():
                buf = io.BytesIO()
                with wave.open(buf, "wb") as wf:
                    voice.synthesize_wav(text, wf, syn_config=cfg)
                return to_mp3(buf.getvalue())

            run("piper", name, cid, synth)


# ---------------------------------------------------------------- Google Translate (gTTS)
# Free, unofficial use of Google Translate's speech. Fine for a test, not for production.
def provider_gtts():
    from gtts import gTTS

    def synth(text, slow):
        buf = io.BytesIO()
        gTTS(text, lang="is", slow=slow).write_to_fp(buf)
        return buf.getvalue()

    for cid, text, slow in clips_with_english():
        run("gtts", "Google Translate (is)", cid, lambda: synth(text, slow),
            note="Unofficial Google Translate voice")


PROVIDERS = {
    "edge": provider_edge,
    "azure": provider_azure,
    "elevenlabs": provider_elevenlabs,
    "piper": provider_piper,
    "gtts": provider_gtts,
}


def main():
    wanted = sys.argv[1:] or list(PROVIDERS)
    OUT.mkdir(exist_ok=True)
    mf = OUT / "manifest.json"
    old = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {"entries": []}
    # Keep results from providers we aren't re-running.
    kept = [e for e in old["entries"] if e["provider"] not in wanted]

    for name in wanted:
        print(f"\n== {name}")
        try:
            PROVIDERS[name]()
        except Exception:
            traceback.print_exc()
            record(name, "-", "-", error="provider failed, see console")

    data = {
        "clips": {cid: {"label": c["label"], "text": c["text"]} for cid, c in CLIPS.items()},
        "english_text": ENGLISH_SAMPLE,
        "entries": kept + manifest,
    }
    mf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for e in manifest if e["file"])
    print(f"\nDone: {ok} clips written, {len(manifest) - ok} failed. Manifest: {mf}")


if __name__ == "__main__":
    main()
