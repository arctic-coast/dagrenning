"""Fetch the day's facts: weather, sun times and news headlines.

Sources (all free, no keys):
- Veðurstofa Íslands (Icelandic Met Office) point forecast for Húsavík, in Icelandic
- MET Norway sunrise API for sunrise and sunset
- RÚV news RSS feed
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone

import requests

USER_AGENT = "dagrenning/0.1 (personal Icelandic learning podcast)"
TIMEOUT = 30

# Húsavík. Iceland is on UTC all year, so local time == UTC.
PLACE = "Húsavík"
LAT, LON = 66.0449, -17.3389
VEDUR_STATION = "3696"  # Húsavík


@dataclass
class HourForecast:
    time: str          # "HH:MM"
    temp_c: int | None
    wind_ms: int | None
    wind_dir: str      # Icelandic compass, e.g. "NA"
    description: str   # Icelandic, e.g. "Alskýjað"


@dataclass
class DayFacts:
    place: str
    date: str                     # ISO date
    weekday_index: int            # 0 = Monday
    generated_at: str             # "HH:MM" local time
    sunrise: str | None
    sunset: str | None
    forecast: list[HourForecast] = field(default_factory=list)
    news: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def fetch_forecast(day: date) -> list[HourForecast]:
    url = "https://xmlweather.vedur.is/"
    params = {"op_w": "xml", "type": "forec", "lang": "is", "view": "xml", "ids": VEDUR_STATION}
    r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for f in root.iter("forecast"):
        ftime = f.findtext("ftime", "")
        try:
            t = datetime.strptime(ftime, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if t.date() != day or not (6 <= t.hour <= 23):
            continue

        def num(tag):
            v = (f.findtext(tag) or "").strip()
            return int(v) if re.fullmatch(r"-?\d+", v) else None

        out.append(HourForecast(
            time=t.strftime("%H:%M"),
            temp_c=num("T"),
            wind_ms=num("F"),
            wind_dir=(f.findtext("D") or "").strip(),
            description=(f.findtext("W") or "").strip(),
        ))
    return out


def fetch_sun(day: date) -> tuple[str | None, str | None]:
    url = "https://api.met.no/weatherapi/sunrise/3.0/sun"
    params = {"lat": LAT, "lon": LON, "date": day.isoformat(), "offset": "+00:00"}
    r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    props = r.json().get("properties", {})

    def hhmm(key):
        t = (props.get(key) or {}).get("time")
        return t[11:16] if t else None  # "2026-11-08T09:30+00:00" -> "09:30"

    return hhmm("sunrise"), hhmm("sunset")


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def fetch_news(limit: int = 15) -> list[dict]:
    r = requests.get("https://www.ruv.is/rss/frettir", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for it in root.iter("item"):
        desc = _clean(it.findtext("description", ""))
        items.append({
            "title": _clean(it.findtext("title", "")),
            "summary": desc[:500],
            "link": (it.findtext("link") or "").strip(),
            "published": (it.findtext("pubDate") or "").strip(),
        })
        if len(items) >= limit:
            break
    return items


def gather(day: date | None = None, with_news: bool = True) -> DayFacts:
    now = datetime.now(timezone.utc)
    day = day or now.date()
    # Made ahead of time (e.g. the evening before): assume it's heard first thing in the morning.
    heard_at = now.strftime("%H:%M") if day == now.date() else "06:30"
    facts = DayFacts(
        place=PLACE, date=day.isoformat(), weekday_index=day.weekday(),
        generated_at=heard_at, sunrise=None, sunset=None,
    )
    # Each source is optional: a missing one shouldn't stop the episode.
    try:
        facts.forecast = fetch_forecast(day)
    except Exception as e:
        facts.problems.append(f"weather: {e}")
    try:
        facts.sunrise, facts.sunset = fetch_sun(day)
    except Exception as e:
        facts.problems.append(f"sun: {e}")
    if with_news:
        try:
            facts.news = fetch_news()
        except Exception as e:
            facts.problems.append(f"news: {e}")
    return facts


if __name__ == "__main__":
    import json
    print(json.dumps(gather().to_dict(), ensure_ascii=False, indent=2))
