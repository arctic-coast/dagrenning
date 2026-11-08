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
    pause_after: bool = Field(description="True to leave a few seconds of silence afterwards, e.g. for the listener to repeat or answer.")
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
    news_links: list[str] = Field(description="Links of the RÚV stories used.")


SYSTEM = """\
You write the script for "Icepod", a short daily podcast that helps one listener learn Icelandic.
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
   c. News: two or three stories from the RÚV headlines provided. Prefer stories about north Iceland,
      Iceland generally, weather, nature, fishing and sport; skip gossip and celebrity stories. Two or three
      very simple Icelandic sentences (A2) per story. Never invent facts beyond the headline and summary
      provided. If no news is provided, skip this part.
   Use short Icelandic headings to mark the parts, e.g. "Veðrið í dag." and "Fréttir dagsins."
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
   Icelandic, then the sentence (Icelandic, slow), then its translation (English), then the sentence again
   (Icelandic, slow) with a pause after so the listener can repeat it.
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


def build_prompt(facts: dict, recent_words: list[str]) -> str:
    weekday = WEEKDAYS_IS[facts["weekday_index"]]
    return (
        f"Today's facts as JSON. The weekday in Icelandic is {weekday}. "
        f"Times are local time in Iceland.\n\n"
        f"{json.dumps(facts, ensure_ascii=False, indent=1)}\n\n"
        f"Recently taught words (don't reuse): {', '.join(recent_words) if recent_words else 'none yet'}\n\n"
        "Write today's episode."
    )


def write_episode(facts: dict, recent_words: list[str]) -> Episode:
    client = anthropic.Anthropic()
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        output_config={"effort": "medium"},
        # If a safety classifier ever declines, the API retries on a fallback model in the same call.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM,
        messages=[{"role": "user", "content": build_prompt(facts, recent_words)}],
        output_format=Episode,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Claude declined to write the episode: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("The script was cut off (max_tokens).")
    if response.parsed_output is None:
        raise RuntimeError("Claude's reply couldn't be read as an episode.")
    return response.parsed_output
