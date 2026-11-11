"""Make today's Dagrenning episode.

    uv run python make_episode.py                  # full run: data + Claude + audio
    uv run python make_episode.py --no-news        # weather and lessons only
    uv run python make_episode.py --script x.json  # skip Claude, voice an existing script

Output goes to episodes/<date>/: episode.mp3, notes.md (the written version),
script.json, facts.json and timings.json (when each line and word is spoken,
for the read-along player). Taught words are remembered in episodes/history.json
so they aren't repeated for a while.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from icepod import audio, data  # noqa: E402
from icepod.script import Episode, write_episode  # noqa: E402

import os  # noqa: E402

# The daily GitHub job points this at the published site folder, so episodes and word history persist there.
EPISODES = Path(os.getenv("ICEPOD_EPISODES_DIR", ROOT / "episodes"))
HISTORY = EPISODES / "history.json"
RECENT_DAYS = 30


def load_history() -> dict:
    if HISTORY.exists():
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    return {"days": {}}


def recent_words(history: dict, today: str) -> list[str]:
    days = sorted(d for d in history["days"] if d < today)[-RECENT_DAYS:]
    return [w for d in days for w in history["days"][d]]


def notes_markdown(ep: Episode, facts: dict) -> str:
    def cell(t):
        return t.replace("|", "\\|").replace("\n", " ")

    lines = [f"# {ep.title}", "", f"{facts['place']} · {facts['date']}", ""]
    lines += ["## Transcript", "",
              "Left: what you hear in Icelandic. Right: the English. *Italics* = spoken in English.", "",
              "| Íslenska | English |", "|---|---|"]
    for s in ep.segments:
        if s.lang == "is":
            lines.append(f"| {cell(s.text)} | {cell(s.translation)} |")
        else:
            lines.append(f"| | *{cell(s.text)}* |")
    lines += ["", "## Words of the day", "", "| Íslenska | English |", "|---|---|"]
    lines += [f"| {cell(w.icelandic)} | {cell(w.english)} |" for w in ep.words]
    lines += ["", "## Sentence of the day", "", f"> {ep.sentence.icelandic}", ">", f"> {ep.sentence.english}", ""]
    lines += ["## Phrases of the day", "", "| Íslenska | English |", "|---|---|"]
    lines += [f"| {cell(p.icelandic)} | {cell(p.english)} |" for p in ep.phrases]
    lines.append("")
    if ep.news_links:
        lines += ["## Sources", "", *[f"- {u}" for u in ep.news_links],
                  "- Weather: Veðurstofa Íslands. Sun times: MET Norway.", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--script", help="voice an existing script.json instead of asking Claude")
    ap.add_argument("--date", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--skip-if-exists", action="store_true",
                    help="do nothing if this date's episode is already made (for backup runs)")
    args = ap.parse_args()

    day = date.fromisoformat(args.date) if args.date else None
    if args.skip_if_exists:
        target = (day or datetime.now(timezone.utc).date()).isoformat()
        if (EPISODES / target / "script.json").exists():
            print(f"Episode for {target} already exists; nothing to do.")
            return 0
    print("Fetching weather, sun times and news...")
    facts = data.gather(day, with_news=not args.no_news).to_dict()
    for p in facts["problems"]:
        print(f"  warning: {p}")
    out_dir = EPISODES / facts["date"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")

    history = load_history()
    if args.script:
        ep = Episode.model_validate_json(Path(args.script).read_text(encoding="utf-8"))
        print(f"Using script from {args.script}")
    else:
        print("Writing the script with Claude...")
        ep = write_episode(facts, recent_words(history, facts["date"]))
    (out_dir / "script.json").write_text(ep.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "notes.md").write_text(notes_markdown(ep, facts), encoding="utf-8")

    print(f"Recording {len(ep.segments)} segments...")
    mp3, timings = audio.render(ep.segments)
    (out_dir / "episode.mp3").write_bytes(mp3)
    (out_dir / "timings.json").write_text(json.dumps(timings, ensure_ascii=False, indent=1), encoding="utf-8")

    history["days"][facts["date"]] = [w.icelandic for w in ep.words]
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    words = sum(len(s.text.split()) for s in ep.segments)
    print(f"\nDone: {out_dir / 'episode.mp3'} ({len(mp3) // 1024} KB, ~{words} words, "
          f"about {audio.mp3_seconds(mp3) / 60:.1f} min)")


if __name__ == "__main__":
    sys.exit(main())
