# Dagrenning

A daily 3–4 minute Icelandic learning podcast from Húsavík, for A1–A2 learners. *Dagrenning* means "daybreak".

Each episode: the date, weather and news in Icelandic only; five words of the day with example
sentences; a sentence of the day; and phrases from it. The transcript shows the English next to
every Icelandic line. On each episode page the transcript is a read-along player: the line being
spoken is shown large and each Icelandic word is highlighted as it is said (timings come from the
voice service and are saved as `timings.json`). Tap a line to jump to it.

- **Weather:** Veðurstofa Íslands (Húsavík forecast). **Sun times:** MET Norway. **News:** RÚV.
- **Script:** written by Claude (`icepod/script.py`).
- **Voices:** Microsoft Gunnar (Icelandic) and Sonia (English) (`icepod/audio.py`).
- **Daily run:** GitHub Actions at 05:15 UTC (`.github/workflows/daily.yml`), published to GitHub Pages.

## Run locally

```
uv run python make_episode.py
```

Keys go in `.env` (see `.env.example`). Only `ANTHROPIC_API_KEY` is required.
