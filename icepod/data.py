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
    local_notices: list[dict] = field(default_factory=list)   # Norðurþing municipality news
    events: list[dict] = field(default_factory=list)          # North Iceland + Akureyri listings
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
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or "")).replace("\xad", "")  # drop soft hyphens
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


BROWSER_UA = {"User-Agent": "Mozilla/5.0 (compatible; dagrenning/0.1; personal Icelandic learning podcast)"}


def _get(url: str) -> str:
    r = requests.get(url, headers=BROWSER_UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def _field(block: str, cls: str) -> str:
    m = re.search(rf'<div class="{cls}"[^>]*>(.*?)</div>', block, re.S)
    return _clean(m.group(1)) if m else ""


def fetch_local_notices(day: date, days_back: int = 10, limit: int = 8) -> list[dict]:
    """Recent Norðurþing municipality news: closures, notices, local happenings in Húsavík."""
    base = "https://www.nordurthing.is"
    page = _get(f"{base}/is/frettir")
    out = []
    for block in page.split('<div class="news__item">')[1:]:
        link = re.search(r'href="(/is/frettir/[^"#?]+)"', block)
        posted = re.search(r'<div class="date">\s*(\d{2})\.(\d{2})\.(\d{4})', block)
        if not link or not posted:
            continue
        d = date(int(posted.group(3)), int(posted.group(2)), int(posted.group(1)))
        if (day - d).days > days_back or d > day:
            continue
        cat = re.search(r'<span class="categories[^"]*">(.*?)</span>', block, re.S)
        item = {"title": _clean(re.search(r"<h3>(.*?)</h3>", block, re.S).group(1)),
                "posted": d.isoformat(), "category": _clean(cat.group(1)) if cat else "",
                "link": base + link.group(1), "text": ""}
        try:  # the body: everything after the date/category line, up to "Til baka"
            body = _clean(re.sub(r"<script.*?</script>|<style.*?</style>|<nav.*?</nav>|<header.*?</header>|<footer.*?</footer>",
                                 "", _get(item["link"]), flags=re.S))
            start = body.find(item["category"], body.find(posted.group(0)[-10:])) if item["category"] else -1
            body = body[start + len(item["category"]):] if start >= 0 else body
            item["text"] = body.split("Til baka í yfirlit")[0].strip()[:900]
        except Exception:
            pass
        out.append(item)
        if len(out) >= limit:
            break
    return out


def fetch_north_iceland_events() -> list[dict]:
    """Visit North Iceland's event listing (Icelandic): title, dates, category, area."""
    base = "https://www.northiceland.is"
    page = _get(f"{base}/is/ferdaupplysingar/vidburdir")
    out = []
    for block in page.split('<div class="event__single')[1:]:
        link = re.search(r'href="(/is/ferdaupplysingar/vidburdir/[^"#?]+)"[^>]*>(.*?)</a>', block, re.S)
        if not link or link.group(1).endswith(("/submit", "/calendar")):
            continue
        classes = block.split('"', 1)[0]
        area = next((c for c in classes.split() if "iceland" in c), "")  # e.g. husavik-nordurthing-northeastern-region-640-iceland
        out.append({"source": "Visit North Iceland", "title": _clean(link.group(2)),
                    "dates": _field(block, "date"), "category": _field(block, "category"),
                    "area": area.replace("-iceland", "").replace("-northeastern-region", ""),
                    "link": base + link.group(1)})
    return out


def fetch_akureyri_events() -> list[dict]:
    """Visit Akureyri's 'what's on': dates, time, venue and a one-line description."""
    base = "https://www.visitakureyri.is"
    page = _get(f"{base}/en/whats-on")
    out = []
    for block in re.split(r'<div class="event (?=[^"]*month)', page)[1:]:
        link = re.search(r'href="(/en/whats-on/[^"#?]+)"[^>]*>(.*?)</a>', block, re.S)
        if not link or link.group(1).endswith("/submit"):
            continue
        cats = [_clean(c) for c in re.findall(r'<span class="event__category">(.*?)</span>', block, re.S)]
        if "Exhibitions" in cats:  # months-long exhibitions, not "this week" events
            continue
        out.append({"source": "Visit Akureyri", "title": _clean(link.group(2)),
                    "dates": _field(block, "date"), "time": _field(block, "time"),
                    "venue": _field(block, "location"), "description": _field(block, "description")[:200],
                    "categories": cats, "link": base + link.group(1)})
    return out


def gather(day: date | None = None, with_news: bool = True) -> DayFacts:
    now = datetime.now(timezone.utc)
    day = day or now.date()
    # Made ahead of time (e.g. the evening before): assume it's heard first thing in the morning.
    # Episodes go out at 06:30, so never say an earlier time than that.
    heard_at = max(now.strftime("%H:%M"), "06:30") if day == now.date() else "06:30"
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
        try:
            facts.local_notices = fetch_local_notices(day)
        except Exception as e:
            facts.problems.append(f"Norðurþing notices: {e}")
        for name, fetch in (("North Iceland events", fetch_north_iceland_events),
                            ("Akureyri events", fetch_akureyri_events)):
            try:
                facts.events += fetch()
            except Exception as e:
                facts.problems.append(f"{name}: {e}")
    return facts


if __name__ == "__main__":
    import json
    print(json.dumps(gather().to_dict(), ensure_ascii=False, indent=2))
