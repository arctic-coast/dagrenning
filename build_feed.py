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
from icepod.audio import mp3_seconds  # noqa: E402


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


def page(title: str, body: str, css: str = "") -> str:
    return (f'<!doctype html><html lang="is"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{PAGE_CSS}{css}</style></head><body>{body}</body></html>")


# Read-along player: audio and the line being spoken stay pinned at the top,
# the full transcript scrolls underneath. Built from timings.json.
PLAYER_CSS = """
.player{position:sticky;top:0;z-index:2;background:#f6f8f9;margin:0 -16px;padding:10px 16px 12px;
border-bottom:1px solid #d6dde0}
.now{min-height:6.2em;padding-top:8px}
.now-is{font-size:1.45rem;line-height:1.35;font-weight:600;margin:0}
.now-is.en-only{font-size:1.1rem;font-weight:400;font-style:italic;color:#5b6b72}
.now-en{font-size:.98rem;color:#5b6b72;margin:.35em 0 0}
.w{border-radius:4px;padding:0 1px;margin:0 -1px;transition:background-color .08s}
.w.on{background:#ffd95e;color:#1d2529}
.lines{list-style:none;padding:0;margin:0}
.lines li{padding:10px 12px;margin:0 -12px;border-radius:10px;cursor:pointer;-webkit-tap-highlight-color:transparent}
.lines li:hover{background:#eaeff1}
.lines li.cur{background:#dcebf1}
.lines li:focus-visible{outline:2px solid #1f6f8b}
.lines .is-text{display:block;font-weight:500;font-size:1.05rem}
.lines .tr{display:block;color:#5b6b72;font-size:.92rem}
.lines li.en-only{color:#5b6b72;font-style:italic;font-size:.95rem}
.hint{color:#5b6b72;font-size:.9rem}
@media (prefers-color-scheme:dark){
.player{background:#12181b;border-color:#2c373c}
.now-is.en-only,.now-en,.lines .tr,.lines li.en-only,.hint{color:#9aaab0}
.w.on{background:#f2c94c;color:#12181b}
.lines li:hover{background:#1b2428}.lines li.cur{background:#1d3640}}
"""

PLAYER_JS = """
(function(){
var T=JSON.parse(document.getElementById('timings').textContent);
var audio=document.getElementById('audio'),nowIs=document.getElementById('now-is'),nowEn=document.getElementById('now-en');
var lis=document.querySelectorAll('#lines li');
var cur=-1,curWord=-1,nowWords=[],pending=null,lastUserScroll=0,raf=0;
function wordsOf(el){var a=[];el.querySelectorAll('.w').forEach(function(s){a[+s.dataset.k]=s});return a}
var lineWords=[].map.call(lis,function(li){var t=li.querySelector('.is-text');return t?wordsOf(t):[]});
function show(i){
  var li=lis[i],t=li.querySelector('.is-text'),tr=li.querySelector('.tr');
  if(t){nowIs.innerHTML=t.innerHTML;nowIs.className='now-is';nowEn.textContent=tr?tr.textContent:''}
  else{nowIs.textContent=li.textContent;nowIs.className='now-is en-only';nowEn.textContent=''}
  nowWords=wordsOf(nowIs);
}
function setWord(k){
  if(k===curWord)return;
  if(curWord>=0){var a=lineWords[cur][curWord],b=nowWords[curWord];if(a)a.classList.remove('on');if(b)b.classList.remove('on')}
  curWord=k;
  if(k>=0){var c=lineWords[cur][k],d=nowWords[k];if(c)c.classList.add('on');if(d)d.classList.add('on')}
}
function update(){
  var t=audio.currentTime,i=0;
  while(i+1<T.length&&T[i+1][0]<=t+0.03)i++;
  if(i!==cur){
    setWord(-1);
    if(cur>=0)lis[cur].classList.remove('cur');
    cur=i;lis[i].classList.add('cur');show(i);
    if(!audio.paused&&Date.now()-lastUserScroll>4000)lis[i].scrollIntoView({block:'center',behavior:'smooth'});
  }
  var s=T[i],k=-1;
  if(t>=s[0]&&t<s[1]){var w=s[2];for(var j=0;j<w.length;j++){if(w[j]<=t+0.03)k=j;else break}}
  setWord(k);
}
function loop(){update();raf=audio.paused?0:requestAnimationFrame(loop)}
audio.addEventListener('play',function(){if(!raf)raf=requestAnimationFrame(loop)});
['pause','seeked','timeupdate','ended'].forEach(function(e){audio.addEventListener(e,update)});
audio.addEventListener('loadedmetadata',function(){if(pending!==null){audio.currentTime=pending;pending=null}});
function seek(i){
  var t=T[i][0]+0.01;
  if(audio.readyState>=1)audio.currentTime=t;else pending=t;
  var p=audio.play();if(p&&p.catch)p.catch(function(){});
  lastUserScroll=0;
}
lis.forEach(function(li,i){
  li.addEventListener('click',function(){seek(i)});
  li.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();seek(i)}});
});
['wheel','touchmove'].forEach(function(e){window.addEventListener(e,function(){lastUserScroll=Date.now()},{passive:true})});
show(0);
})();
"""


def _line_html(seg: dict) -> str:
    """One transcript line; Icelandic words that have timings are wrapped so they can be highlighted."""
    text = seg["text"]
    if seg["lang"] != "is":
        return f'<li class="en-only" lang="en" role="button" tabindex="0">{html.escape(text)}</li>'
    parts, pos = [], 0
    for k, w in enumerate(seg.get("words", [])):
        at = w.get("at")
        if at is None or at < pos:
            continue
        end = at + len(w["text"])
        parts += [html.escape(text[pos:at]), f'<span class="w" data-k="{k}">{html.escape(text[at:end])}</span>']
        pos = end
    parts.append(html.escape(text[pos:]))
    tr = f'<span class="tr" lang="en">{html.escape(seg["translation"])}</span>' if seg.get("translation") else ""
    return f'<li role="button" tabindex="0"><span class="is-text">{"".join(parts)}</span>{tr}</li>'


def player_page(title: str, notes_md: str, timings: dict, audio_src: str) -> str:
    # The notes' own transcript table is replaced by the player; the rest (words, sentence, phrases) stays.
    head, _, rest = notes_md.partition("## Transcript")
    rest = rest[rest.find("\n## "):] if "\n## " in rest else ""
    segs = timings["segments"]
    data = [[s["start"], s["end"], [w["start"] for w in s.get("words", [])]] for s in segs]
    body = (
        f'<p><a href="../../">← Dagrenning</a></p>'
        + markdown.markdown(head, extensions=["tables"])
        + '<div class="player">'
        f'<audio id="audio" controls preload="metadata" src="{audio_src}"></audio>'
        '<div class="now" aria-live="off"><p class="now-is" id="now-is" lang="is"></p>'
        '<p class="now-en" id="now-en" lang="en"></p></div></div>'
        '<h2>Transcript</h2><p class="hint">Tap a line to hear it. <em>Italics</em> = spoken in English.</p>'
        f'<ol class="lines" id="lines">{"".join(_line_html(s) for s in segs)}</ol>'
        + markdown.markdown(rest, extensions=["tables"])
        + '<script type="application/json" id="timings">'
        + json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
        + f"</script><script>{PLAYER_JS}</script>"
    )
    return page(title, body, PLAYER_CSS)


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
        timings = d / "timings.json"
        if timings.exists():  # read-along player
            (d / "index.html").write_text(player_page(
                ep["title"], notes_md, json.loads(timings.read_text(encoding="utf-8")),
                f"../../audio/{d.name}.mp3"), encoding="utf-8")
        else:  # older episodes: plain transcript
            (d / "index.html").write_text(page(
                ep["title"],
                f'<p><a href="../../">← Dagrenning</a></p><audio controls preload="none" src="../../audio/{d.name}.mp3"></audio>'
                + notes_html), encoding="utf-8")
        size = mp3.stat().st_size
        episodes.append({
            "date": d.name, "title": ep["title"], "audio": audio_url, "page": page_url,
            # Count MP3 frames: episodes can differ in bitrate (Microsoft 48 kbps, ElevenLabs 64 kbps).
            "size": size, "seconds": int(mp3_seconds(mp3.read_bytes())), "notes_html": notes_html,
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
    <description>{html.escape("Read along with highlighted words: " + e["page"] + " . Orð dagsins: " + e["words"] + ".")}</description>
    <content:encoded><![CDATA[<p><a href="{e["page"]}"><strong>Read along: the Icelandic text with each word highlighted as it's spoken</strong></a></p>{e["notes_html"]}]]></content:encoded>
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
