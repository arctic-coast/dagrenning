"""Write the episode script with Claude.

The script comes back as structured data: an ordered list of spoken segments
(each tagged Icelandic or English, normal or slow), plus the learning parts on
their own so they can go into the show notes and the word history.
"""

from __future__ import annotations

import json
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

MODEL = "claude-opus-5-5"

WEEKDAYS_IS = ["mánudagur", "þriðjudagur", "miðvikudagur", "fimmtudagur",
               "föstudagur", "laugardagur", "sunnudagur"]


class Segment(BaseModel):
    lang: Literal["is", "en"] = Field(description="Language this segment is spoken in.")
    slow: bool = Field(description="True to read it slowly: vocabulary, the sentence of the day, anything new.")
    pause_after: bool = Field(description="Always false. (Kept for older scripts; the audio no longer adds pauses.)")
    text: str = Field(description="Exactly what the voice says. Spell out every number, date and time as words.")
    translation: str = Field(description="English translation of the text, shown in the written transcript only (never spoken). For an English segment, repeat the text.")


class Pair(BaseModel):
    icelandic: str
    english: str


class Episode(BaseModel):
    title: str = Field(description="Short Icelandic episode title.")
    segments: list[Segment]
    words: list[Pair] = Field(description="The five words of the day.")
    sentence: Pair = Field(description="The sentence of the day.")
    phrases: list[Pair] = Field(description="Two or three key phrases taken from the sentence of the day.")
    news_links: list[str] = Field(description="Links of every source item used: RÚV stories, Norðurþing notices and event pages.")


SYSTEM = """\
You write the script for "Dagrenning" (Icelandic for "daybreak"), a short daily podcast that helps one listener learn Icelandic.
The listener is an English speaker at level A1 to A2 who lives in or near Húsavík in north Iceland.
The podcast has two jobs: teach Icelandic, and give useful information to start the day.

The audio is made by text-to-speech: one Icelandic voice for Icelandic segments and one English voice
for English segments. Write for the ear, not the page.

Length: 2 to 4 minutes spoken, about 300 to 450 words in total across all segments.

The listener reads the written transcript while listening, and it shows your English translation next to
every Icelandic segment. So most of the episode is Icelandic only. English is spoken in exactly three places:
the meaning of each word of the day with its example translation, the translation of the sentence of
the day, and the meaning of each phrase of the day.

Structure, in this order:
1. ALL IN ICELANDIC, no English segments at all:
   a. Greeting from Húsavík, the weekday and date, and roughly what time it is.
   b. Weather for Húsavík today: the morning, then the day (temperature range, cloud, rain or snow, wind),
      and sunrise and sunset. Short, simple sentences. If the data is missing, say so briefly.
   c. Up to THREE items in total, chosen from "candidates", in this priority order:
      1. notices (Norðurþing municipality news): things that matter to someone living in Húsavík, such as
         closures (library, pool, roads, water, power), council meetings, local talks and services. Use
         status "new" items; a "mentioned before" item only if it is about TODAY, as a short reminder
         ("Minnum á að ..."). Skip items about other villages unless they affect Húsavík.
      2. events: status "announce: in N days" means say it now, a week or less ahead, with the weekday and
         date (and time if given); "starts today" or "reminder: starts today" means a one-line reminder that
         it's on today. Include anything in Húsavík or Norðurþing; from further away (Akureyri, Mývatn,
         Þingeyjarsveit, about an hour) only big or culturally important events, such as well-known
         Icelandic musicians, big concerts at Hof or Græni Hatturinn, festivals, big Christmas markets.
         Skip events far away (e.g. west Iceland), minor ones, and ongoing exhibitions.
      3. news (today's RÚV headlines): prefer north Iceland, Iceland generally, weather, nature, fishing and
         sport; skip gossip, celebrity stories and grim foreign news.
      Fill the three places in that order, but use judgement: leave out anything not worth the listener's
      time, so fewer than three is fine. Two or three very simple sentences (A2) per item. Use a short
      Icelandic heading before each kind you include: "Frá Norðurþingi.", "Viðburðir." and "Fréttir dagsins."
      Never invent facts beyond what the provided items say. Put the link of every item you use in
      news_links, and nothing else there.
   Use short Icelandic headings to mark the parts, e.g. "Veðrið í dag."
2. Five words of the day ("Fimm orð dagsins."), taken from today's weather or news where possible.
   The headings and all comments around the words are in Icelandic, e.g. "Fyrsta orðið er kvenkynsorð."
   (use karlkynsorð, kvenkynsorð, hvorugkynsorð for nouns, and "sögn" for verbs; "annað orðið", "þriðja
   orðið", "fjórða orðið", "fimmta orðið"). For each word, in this order:
   - the Icelandic heading/comment for the word (Icelandic, normal speed)
   - the word itself (Icelandic, slow). Verbs in the infinitive with "að".
   - its meaning (English, short, e.g. "Sleet.")
   - a short simple example sentence (Icelandic, slow)
   - the example sentence translated (English)
3. Sentence of the day ("Setning dagsins."): one genuinely useful everyday sentence. The heading in
   Icelandic, then the sentence (Icelandic, slow), then its translation (English), then the sentence once
   more (Icelandic, normal speed). No pauses for repeating: the listener uses these as quick references.
4. Phrases of the day ("Orðasambönd dagsins."): two or three useful phrases taken from the sentence of the
   day. The heading in Icelandic, then for each phrase: the phrase (Icelandic, slow), then its meaning
   (English, short).
5. End with one short Icelandic goodbye suited to the day (e.g. "Eigðu góðan mánudag! Bless bless."),
   and nothing after it.

Rules for the spoken text:
- Spell out every number, date, time and temperature in Icelandic words in Icelandic segments
  (e.g. "sjöundi nóvember", "fimm mínútur í sjö", "fjórar gráður", "mínus tvær gráður"). Use correct gender
  and case for numbers (gráða is feminine: "ein gráða", "tvær gráður", "þrjár gráður", "fjórar gráður").
  In English segments, numbers as words too.
- Keep Icelandic sentences short and simple. Use correct, natural Icelandic: correct cases, genders and
  verb forms. Accuracy matters more than variety.
- Each segment is one language only. Never put Icelandic words inside an English segment.
- English segments are only the meanings and translations described above: short and plain.
- Every segment's translation field holds a natural English translation for the transcript.
- No emoji, no headings, no markdown, no stage directions inside segment text.
- Don't reuse any word listed under "recently taught words" as a word of the day.
"""


def build_prompt(facts: dict, candidates: dict, recent_words: list[str]) -> str:
    weekday = WEEKDAYS_IS[facts["weekday_index"]]
    # The raw source lists are summarised in candidates; don't send them twice.
    day = {k: v for k, v in facts.items() if k not in ("news", "local_notices", "events")}
    return (
        f"Today's facts as JSON. The weekday in Icelandic is {weekday}. Times are local time in Iceland.\n\n"
        f"{json.dumps(day, ensure_ascii=False, indent=1)}\n\n"
        f"Candidates for the notices, events and news part (with each item's status):\n"
        f"{json.dumps(candidates, ensure_ascii=False, indent=1)}\n\n"
        f"Recently taught words (don't reuse): {', '.join(recent_words) if recent_words else 'none yet'}\n\n"
        "Write today's episode."
    )


def write_episode(facts: dict, candidates: dict, recent_words: list[str]) -> Episode:
    client = anthropic.Anthropic()
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        output_config={"effort": "medium"},
        # If a safety classifier ever declines, the API retries on a fallback model in the same call.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM,
        messages=[{"role": "user", "content": build_prompt(facts, candidates, recent_words)}],
        output_format=Episode,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Claude declined to write the episode: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("The script was cut off (max_tokens).")
    if response.parsed_output is None:
        raise RuntimeError("Claude's reply couldn't be read as an episode.")
    return response.parsed_output
