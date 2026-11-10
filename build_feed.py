"""Build the podcast website: feed.xml, one page per episode, an index and cover art.

    uv run python build_feed.py <site_dir> <base_url>

<site_dir>/episodes/<date>/ holds what make_episode.py writes. This copies each
episode's MP3 to <site_dir>/audio/<date>.mp3 (a short, stable URL for podcast
apps), renders notes.md into an HTML page, and writes an RSS feed that podcast
apps can subscribe to. Episodes older than KEEP_DAYS are deleted.
"""

from __future__ import annotations

import html
import json
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import markdown
from PIL import Image, ImageDraw, ImageFont

TITLE = "Dagrenning"
SUBTITLE = "Daily Icelandic from Húsavík: weather, news, five words and a sentence of the day."
AUTHOR = "Dagrenning"
KEEP_DAYS = 60
BITRATE = 48  # kbps, matches icepod.audio


def make_cover(path: Path):
    if path.exists():
        return
    img = Image.new("RGB", (1400, 1400), (24, 58, 74))
    d = ImageDraw.Draw(img)
    d.ellipse((250, 820, 1150, 1720), fill=(214, 228, 233))   # a low winter sun
    d.rectangle((0, 1180, 1400, 1400), fill=(17, 40, 51))     # the bay

    def font(size):
        for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default(size)

    d.text((700, 420), "Dagrenning", font=font(170), fill=(240, 246, 248), anchor="mm")
    d.text((700, 640), "Íslenska frá Húsavík", font=font(72), fill=(170, 205, 216), anchor="mm")
    img.save(path, "PNG", optimize=True)


PAGE_CSS = """
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;max-width:760px;margin:0 auto;
padding:24px max(16px,env(safe-area-inset-left));line-height:1.55;color:#1d2529;background:#f6f8f9}
h1{font-size:1.6rem;line-height:1.25}h2{font-size:1.1rem;margin-top:2em}
table{border-collapse:collapse;width:100%;font-size:.95rem}td,th{border-bottom:1px solid #d6dde0;
padding:6px 8px 6px 0;text-align:left;vertical-align:top}td:first-child{font-weight:500;width:50%}
blockquote{border-left:3px solid #1f6f8b;margin:0;padding:4px 12px}audio{width:100%}
a{color:#1f6f8b}.list li{margin:.4em 0}
@media (prefers-color-scheme:dark){body{background:#12181b;color:#e3e9eb}td,th{border-color:#2c373c}a{color:#6fb6d0}}
"""


def page(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="is"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{PAGE_CSS}</style></head><body>{body}</body></html>")


def main():
    site, base = Path(sys.argv[1]), sys.argv[2].rstrip("/") + "/"
    eps_dir, audio_dir = site / "episodes", site / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    make_cover(site / "cover.png")

    cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    episodes = []
    for d in sorted(eps_dir.glob("20??-??-??"), reverse=True):
        if d.name < cutoff:
            shutil.rmtree(d)
            (audio_dir / f"{d.name}.mp3").unlink(missing_ok=True)
            continue
        script, mp3 = d / "script.json", audio_dir / f"{d.name}.mp3"
        if (d / "episode.mp3").exists():  # newly made: move it to its short public address
            shutil.move(d / "episode.mp3", mp3)
        if not mp3.exists() or not script.exists():
            continue
        ep = json.loads(script.read_text(encoding="utf-8"))
        notes_md = (d / "notes.md").read_text(encoding="utf-8") if (d / "notes.md").exists() else ""
        notes_html = markdown.markdown(notes_md, extensions=["tables"])
        audio_url = f"{base}audio/{d.name}.mp3"
        page_url = f"{base}episodes/{d.name}/"
        (d / "index.html").write_text(page(
            ep["title"],
            f'<p><a href="../../">← Dagrenning</a></p><audio controls preload="none" src="../../audio/{d.name}.mp3"></audio>'
            + notes_html), encoding="utf-8")
        size = mp3.stat().st_size
        episodes.append({
            "date": d.name, "title": ep["title"], "audio": audio_url, "page": page_url,
            "size": size, "seconds": int(size * 8 / (BITRATE * 1000)), "notes_html": notes_html,
            "words": ", ".join(f'{w["icelandic"]} ({w["english"]})' for w in ep.get("words", [])),
        })
    # Clean up audio files whose episode folder is gone.
    keep = {e["date"] for e in episodes}
    for f in audio_dir.glob("*.mp3"):
        if f.stem not in keep:
            f.unlink()

    now = format_datetime(datetime.now(timezone.utc))
    items = []
    for e in episodes:
        pub = format_datetime(datetime.fromisoformat(e["date"]).replace(hour=5, tzinfo=timezone.utc))
        items.append(f"""
  <item>
    <title>{html.escape(e["date"])}: {html.escape(e["title"])}</title>
    <link>{e["page"]}</link>
    <guid isPermaLink="false">dagrenning-{e["date"]}</guid>
    <pubDate>{pub}</pubDate>
    <description>{html.escape("Orð dagsins: " + e["words"] + ". Transcript: " + e["page"])}</description>
    <content:encoded><![CDATA[{e["notes_html"]}]]></content:encoded>
    <enclosure url="{e["audio"]}" length="{e["size"]}" type="audio/mpeg"/>
    <itunes:duration>{e["seconds"]}</itunes:duration>
    <itunes:explicit>false</itunes:explicit>
  </item>""")
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>{TITLE}</title>
  <link>{base}</link>
  <atom:link href="{base}feed.xml" rel="self" type="application/rss+xml"/>
  <description>{html.escape(SUBTITLE)}</description>
  <language>is</language>
  <lastBuildDate>{now}</lastBuildDate>
  <itunes:author>{AUTHOR}</itunes:author>
  <itunes:image href="{base}cover.png"/>
  <itunes:category text="Education"><itunes:category text="Language Learning"/></itunes:category>
  <itunes:explicit>false</itunes:explicit>
  <itunes:block>Yes</itunes:block>{"".join(items)}
</channel>
</rss>
"""
    (site / "feed.xml").write_text(feed, encoding="utf-8")

    rows = "".join(f'<li><a href="episodes/{e["date"]}/">{e["date"]}: {html.escape(e["title"])}</a></li>'
                   for e in episodes)
    (site / "index.html").write_text(page(TITLE, (
        f"<h1>Dagrenning</h1><p>{html.escape(SUBTITLE)}</p>"
        f'<p>Subscribe in a podcast app with this address:<br><code>{base}feed.xml</code></p>'
        f'<ul class="list">{rows or "<li>No episodes yet.</li>"}</ul>')), encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Feed built: {len(episodes)} episode(s) -> {site / 'feed.xml'}")


if __name__ == "__main__":
    main()
