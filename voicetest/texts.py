"""Test texts for the Icelandic voice comparison.

Each clip tests something different:
- passage:  a short episode in miniature, with numbers written out as words
- numerals: the same facts with raw digits, to see if the voice reads them correctly
- names:    place names that are hard to pronounce
- vocab:    words of the day, read slowly the way the podcast would
"""

CLIPS = {
    "passage": {
        "label": "Short episode (numbers written out)",
        "slow": False,
        "text": (
            "Góðan daginn frá Húsavík! "
            "Í dag er laugardagurinn sjöundi nóvember, tvö þúsund tuttugu og sex. "
            "Klukkan er fimm mínútur í sjö. "
            "Veðrið í dag: Í morgun er hitinn fjórar til sex gráður og skýjað. "
            "Seinna í dag gæti hitinn farið upp í níu gráður. "
            "Það verður að mestu þurrt, en vindurinn gæti aukist þegar líður á daginn. "
            "Sólin kemur upp tuttugu og sjö mínútur yfir níu og sest sautján mínútur yfir fjögur. "
            "Í fréttum: Íslenska karlalandsliðið í handbolta vann Úkraínu, fjörutíu og átta gegn tuttugu og sex. "
            "Setning dagsins: Ég er ekki alveg sammála, en ég skil hvað þú átt við. "
            "Eigðu góðan laugardag!"
        ),
    },
    "numerals": {
        "label": "Digits, dates and units (reading test)",
        "slow": False,
        "text": (
            "Laugardagur 07.11.2026. Klukkan er 06:55. "
            "Hiti 4–6°C, vindur 8 m/s. "
            "Sólarupprás kl. 09:27, sólsetur kl. 16:17. "
            "Ísland vann Úkraínu 48–26."
        ),
    },
    "names": {
        "label": "Place names",
        "slow": False,
        "text": (
            "Húsavík. Norðurþing. Hnotasteinn. Eyjafjallajökull. "
            "Þórshöfn. Seyðisfjörður. Reykjavík. Akureyri."
        ),
    },
    "vocab": {
        "label": "Words of the day (slow)",
        "slow": True,
        "text": (
            "Orð dagsins. Að aukast. Að gagnrýna. Að blandast. "
            "Að skora. Að vera sammála."
        ),
    },
}

# An English line, to check how each provider's English voice sits next to the Icelandic.
ENGLISH_SAMPLE = (
    "Sentence of the day: I don't completely agree, but I understand what you mean."
)
