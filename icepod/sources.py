"""A running record of everything the local sources have listed, and which items to offer each day.

Every day the job fetches Norðurþing notices, event listings (Visit North Iceland,
Visit Akureyri) and RÚV news, and merges them into sources.json next to the
episodes. Each item remembers when it was first seen and on which days the
podcast mentioned it.

Each day's candidates (Claude picks up to three, notices first, then events, then news):
- notices: posted recently and not yet mentioned, or mentioned before (a same-day reminder is
  allowed if the notice is about today)
- events: announced once when they're 7 days away or less (status "announce"), then reminded
  on their first day (status "reminder"); nothing in between
- news: today's RÚV headlines not yet mentioned
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

ANNOUNCE_DAYS = 7
KEEP_DAYS = 45

IS_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "maí": 5, "mai": 5, "jún": 6, "jun": 6, "júl": 7,
             "jul": 7, "ágú": 8, "agu": 8, "sep": 9, "okt": 10, "nóv": 11, "nov": 11, "des": 12}
EN_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"])}


def _month(word: str) -> int | None:
    w = word.lower().strip(".")
    if w in EN_MONTHS:
        return EN_MONTHS[w]
    for k, v in IS_MONTHS.items():
        if w.startswith(k):
            return v
    for k, v in EN_MONTHS.items():
        if len(w) >= 3 and k.startswith(w):
            return v
    return None


def parse_dates(text: str, today: date) -> tuple[date | None, date | None]:
    """Start and end date from listing text like '27.-28. nóv', '27. nóv - 4. des', 'November 20',
    'June 6 - May 31'. The year is inferred: the nearest reading that isn't long past."""
    if not text:
        return None, None
    tokens = re.findall(r"\d{1,2}|[A-Za-zÀ-ÿþðæöÞÐÆÖ]+", text)
    english = any(t.lower() in EN_MONTHS for t in tokens)
    pairs: list[tuple[int, int]] = []
    if english:  # month comes first: "November 20 - December 3", "June 6 - May 31"
        month = None
        for t in tokens:
            if t.isdigit() and month:
                pairs.append((int(t), month))
            elif not t.isdigit() and _month(t):
                month = _month(t)
    else:  # day comes first: "27.-28. nóv", "27. nóv - 4. des"
        pending = []
        for t in tokens:
            if t.isdigit():
                pending.append(int(t))
            elif _month(t) and pending:
                pairs += [(d, _month(t)) for d in pending]
                pending = []
    pairs = [(d, m) for d, m in pairs if 1 <= d <= 31]
    if not pairs:
        return None, None

    def make(d, m, year):
        try:
            return date(year, m, d)
        except ValueError:
            return None

    def reading(year):
        s = make(*pairs[0], year)
        e = make(*pairs[-1], year) if s else None
        if s and e and e < s:  # runs over new year
            e = make(*pairs[-1], year + 1)
        return s, e

    start, end = reading(today.year)
    # Only move to next year if the whole run is well past (e.g. "5. jan" seen in November).
    if start and (end or start) < today - timedelta(days=60):
        start, end = reading(today.year + 1)
    return start, end


def load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"items": {}}


def save(store: dict, path: Path):
    path.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")


def update(store: dict, facts: dict, today: date):
    """Merge today's fetched items into the record (keyed by link)."""
    t = today.isoformat()
    items = store["items"]

    def merge(kind, item, extra=None):
        rec = items.setdefault(item["link"], {"kind": kind, "first_seen": t, "mentioned": []})
        rec.update({k: v for k, v in item.items() if k != "link"})
        rec["kind"], rec["last_seen"] = kind, t
        if extra:
            rec.update(extra)

    for n in facts.get("local_notices", []):
        merge("notice", n)
    for e in facts.get("events", []):
        start, end = parse_dates(e.get("dates", ""), today)
        merge("event", e, {"start": start.isoformat() if start else None,
                           "end": end.isoformat() if end else None})
    for n in facts.get("news", []):
        merge("news", n)

    # Forget old things: events long over, notices and news not seen for a while.
    for link in list(items):
        r = items[link]
        last = (r.get("end") or r.get("start")) if r["kind"] == "event" else r.get("last_seen")
        if last and date.fromisoformat(last) < today - timedelta(days=KEEP_DAYS):
            del items[link]


def candidates(store: dict, today: date) -> dict:
    """What Claude may choose from today, each with a status saying why it's offered."""
    t = today.isoformat()
    out = {"notices": [], "events": [], "news": []}
    for link, r in store["items"].items():
        before = [d for d in r.get("mentioned", []) if d < t]
        base = {k: v for k, v in r.items() if k not in ("kind", "mentioned", "first_seen", "last_seen")}
        base["link"] = link
        if r["kind"] == "notice":
            posted = r.get("posted") or r["first_seen"]
            if date.fromisoformat(posted) < today - timedelta(days=10):
                continue
            base["status"] = ("mentioned before: only as a reminder if it's about today" if before
                              else "new")
            out["notices"].append(base)
        elif r["kind"] == "event":
            if not r.get("start"):
                continue
            start = date.fromisoformat(r["start"])
            days = (start - today).days
            if days == 0:
                base["status"] = "reminder: starts today" if before else "starts today"
            elif 0 < days <= ANNOUNCE_DAYS and not before:
                base["status"] = f"announce: in {days} day{'s' if days != 1 else ''}"
            else:
                continue  # already announced, too far ahead, or already started
            out["events"].append(base)
        elif r["kind"] == "news":
            if r.get("last_seen") != t or before:
                continue  # only today's headlines, never repeated
            base["status"] = "today's headline"
            out["news"].append(base)
    out["events"].sort(key=lambda e: e["start"])
    out["notices"].sort(key=lambda n: n.get("posted", ""), reverse=True)
    return out


def record_mentions(store: dict, links: list[str], today: date):
    t = today.isoformat()
    for link in links:
        rec = store["items"].get(link)
        if rec is not None and t not in rec["mentioned"]:
            rec["mentioned"].append(t)
