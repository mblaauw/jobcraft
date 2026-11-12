"""Make a scraped vacancy readable without AI: sections, key facts and what to look at.

Most texts arrive as one long line, with menu text in front. Nothing is thrown away: what
cannot be placed stays available under "other". Words are never changed here.
"""

from __future__ import annotations

import re

from .feedback import STOPWORDS

MAX_SENTENCES = 3
# Phrase, kind. Kinds: role (the work), ask (what they want), offer, about, apply.
_HEADINGS = {
    "role": (
        "dit ga je doen", "wat ga je doen", "wat ga jij doen", "dit is wat je gaat doen", "de functie",
        "functieomschrijving", "functie omschrijving", "over de functie", "over de rol", "jouw functie",
        "jouw rol", "je werkzaamheden", "jouw werkzaamheden", "werkzaamheden", "taken", "de baan",
        "work activities", "job description", "about the role", "the role", "what you will do",
        "what you'll do", "your role", "responsibilities", "project description",
    ),
    "ask": (
        "dit vragen wij", "dit vragen we", "wat vragen wij", "wat vragen we", "wat breng je mee",
        "wat neem je mee", "dit breng je mee", "functie-eisen", "functie eisen", "functie-eisen en competenties",
        "wie ben jij", "wie ben je", "jouw profiel", "jouw kwaliteiten", "wat wij vragen", "wat we vragen",
        "wij vragen", "je profiel", "profiel", "eisen", "qualifications", "requirements",
        "what we ask", "what we ask of you", "your profile", "who you are", "what you bring",
        "specifications", "what we are looking for",
    ),
    "offer": (
        "dit krijg je", "dit bieden we nog meer", "dit bieden wij", "dit bieden we", "wat bieden wij",
        "wat bieden we", "wat wij bieden", "wat we bieden", "wat krijg je", "wat krijg je ervoor",
        "arbeidsvoorwaarden", "wat mag je verwachten", "ons aanbod", "conditions of employment",
        "what we offer", "we offer", "what we can offer you", "benefits",
    ),
    "about": (
        "hier kom je te werken", "over ons", "over de organisatie", "wie zijn wij", "wie zijn we",
        "de organisatie", "organisatie", "over de afdeling", "het team", "jouw team", "je team",
        "waar ga je werken", "about us", "about the organisation", "about the organization",
        "the organisation", "department", "over de functiegroep", "bijzonderheden",
    ),
    "apply": (
        "stel gerust je vraag", "solliciteren", "sollicitatieprocedure", "meer informatie", "contact",
        "interesse", "heb je vragen", "vragen", "hoe solliciteer je", "de sollicitatieprocedure",
        "how to apply", "application", "additional information", "contact information", "procedure",
    ),
}
KIND_TITLES = {
    "role": "Wat je gaat doen",
    "ask": "Wat ze vragen",
    "offer": "Wat je krijgt",
    "about": "Waar je komt te werken",
    "apply": "Reageren",
    "other": "Overige tekst van de pagina",
}
_KIND_OF = {phrase: kind for kind, phrases in _HEADINGS.items() for phrase in phrases}
_ALTERNATIVES = "|".join(re.escape(phrase) for phrase in sorted(_KIND_OF, key=len, reverse=True))
# Flat text has no line breaks: only an unmistakable phrase counts there. A single word such as
# "Organisatie" also occurs mid-sentence, so it needs a line of its own or a colon.
# Dutch headings are often questions ("Wat ga je doen?"): the question mark belongs to the heading.
_SPLIT = re.compile(rf"(?<![\w-])({_ALTERNATIVES})(?=\??(?:\s*:|\s+[A-Z\u00c0-\u00dc]|\s*\n|\s*$))", re.I)
_NOISE_TAIL = re.compile(r"\s(?:Relevante vacatures|Vergelijkbare vacatures|Andere vacatures|Deel deze vacature|Share this)\b.*$", re.I | re.S)
_STRONG_SHORT = {"functieomschrijving", "arbeidsvoorwaarden", "functie-eisen", "work activities", "qualifications", "conditions of employment", "job description"}
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ü])")
_BULLET = re.compile(r"^\s*(?:[•·\-*–]|\d+[.)])\s+")
_INLINE_BULLET = re.compile(r"\s+[•·]\s+")

_MONTHS = "januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december|january|february|march|may|june|july|august|october|december"
_DATE = rf"(?:\d{{1,2}}[-/.]\d{{1,2}}[-/.]\d{{2,4}}|\d{{1,2}}\s+(?:{_MONTHS})(?:\s+\d{{4}})?)"
FACT_PATTERNS = (
    ("Uren", re.compile(r"\b\d{1,2}(?:\s*(?:-|–|tot|to)\s*\d{1,2})?\s*(?:uur|hours)(?=\b)", re.I)),
    ("Salaris", re.compile(r"€\s?[\d.,]+[-,]*(?:\s*(?:-|–|~|tot|to|t/m)\s*€?\s?[\d.,]+[-,]*)?|\b(?:salaris)?schaal\s+\d+(?:\s*(?:-|t/m)\s*\d+)?", re.I)),
    ("Niveau", re.compile(r"\b(?:WO|HBO|MBO|Master(?:'s)?|Bachelor|PhD|universitair|academisch)\b(?:[- ](?:werk- en )?denkniveau)?")),
    ("Contract", re.compile(r"\b(?:vaste aanstelling|vast contract|onbepaalde tijd|bepaalde tijd|tijdelijke? (?:aanstelling|contract)|uitzicht op vast|zicht op vast|detachering|interim|stage|freelance|fixed[- ]term|permanent)\b", re.I)),
    ("Reageer voor", re.compile(rf"(?:sluitingsdatum|(?:solliciteer|solliciteren) (?:voor|vóór|uiterlijk)|reageer (?:voor|vóór|uiterlijk)|reageren (?:kan )?(?:tot|voor)|deadline|closing date|apply before)\W{{0,4}}(?:op\s+)?({_DATE})", re.I)),
)
_IMPORTANT = re.compile(
    r"\b(?:minimaal \d+|tenminste|ten minste|vereist|verplicht|noodzakelijk|moet|must|required|essential|"
    r"VOG|rijbewijs|screening|diploma|afgeronde|hbo|wbo|wo|mbo|\d+\s+jaar)\b",
    re.I,
)


def _clean(text: str) -> str:
    text = re.sub("[\\u200b\\u00a0\\ufeff]", " ", text or "")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _chunks(body: str) -> tuple[list[str], list[str]]:
    """Paragraphs of a few sentences, and whatever was already a list."""
    paragraphs: list[str] = []
    items: list[str] = []
    for line in re.split(r"\n+", body):
        line = line.strip()
        if not line:
            continue
        if _BULLET.match(line):
            items.append(_BULLET.sub("", line))
            continue
        pieces = _INLINE_BULLET.split(line)
        if len(pieces) >= 3:
            paragraphs.extend(piece for piece in pieces[:1] if len(piece.split()) > 3)
            items.extend(pieces[1:] if len(pieces[0].split()) > 3 else pieces)
            continue
        sentences = _SENTENCE.split(line)
        for start in range(0, len(sentences), MAX_SENTENCES):
            paragraphs.append(" ".join(sentences[start : start + MAX_SENTENCES]))
    return paragraphs, items


def _sentences_as_items(section: dict) -> None:
    """In what they ask, each demand deserves its own line to be checked against."""
    if section["items"]:
        return
    sentences = [part for paragraph in section["paragraphs"] for part in _SENTENCE.split(paragraph)]
    if len(sentences) >= 3 and all(len(part) < 240 for part in sentences):
        section["items"], section["paragraphs"] = sentences, []


def _is_heading(match: re.Match, text: str) -> bool:
    phrase = match.group(1)
    if not phrase[0].isupper():
        return False
    before = text[: match.start()].rstrip(" ")
    after = text[match.end() :].removeprefix("?")
    own_line = (not before or before.endswith("\n")) and (after[:1] in ("", "\n", ":") or after.lstrip(" ")[:1] == "\n")
    if own_line or after.lstrip(" ")[:1] == ":":
        return True
    # The pattern ignores case for the phrase, so the capital after it is checked here.
    next_word = after.lstrip()[:1]
    if not next_word.isupper():
        return False
    return len(phrase.split()) >= 3 or phrase.lower() in _STRONG_SHORT


def structure(description: str) -> dict:
    """{sections: [{kind, title, paragraphs, items}], facts: [(label, value)]}"""
    text = _NOISE_TAIL.sub("", _clean(description))
    found = [match for match in _SPLIT.finditer(text) if _is_heading(match, text)]
    sections: list[dict] = []
    other = text[: found[0].start()] if found else text
    for index, match in enumerate(found):
        end = found[index + 1].start() if index + 1 < len(found) else len(text)
        body = text[match.end() : end].lstrip("?").strip(" :\n")
        if len(body) < 40:  # A table of contents, or a word that only looked like a heading.
            if body:
                other += " " + match.group(0) + body
            continue
        heading = match.group(1)
        kind = _KIND_OF[heading.lower()]
        paragraphs, items = _chunks(body)
        section = {"kind": kind, "title": heading[0].upper() + heading[1:], "paragraphs": paragraphs, "items": items}
        if kind == "ask":
            _sentences_as_items(section)
        sections.append(section)
    if not sections:
        paragraphs, items = _chunks(text)
        sections = [{"kind": "other", "title": "", "paragraphs": paragraphs, "items": items}]
        other = ""
    elif other.strip():
        paragraphs, items = _chunks(other)
        sections.append({"kind": "other", "title": KIND_TITLES["other"], "paragraphs": paragraphs, "items": items})
    return {"sections": sections, "facts": facts_of(text)}


def summary(description: str, limit: int = 320) -> str:
    """What the work is, for a quick yes or no: the section about the work when the text has
    one, otherwise its start. The opening of a scraped page is often menu text or a pitch."""
    role = next((part for part in structure(description)["sections"] if part["kind"] == "role"), None)
    lines = [*role["paragraphs"], *role["items"]] if role else []
    text = " ".join(line if line.endswith((".", "!", "?", ":")) else line + "." for line in lines)
    return " ".join((text or description or "").split())[:limit]


def facts_of(text: str) -> list[tuple[str, str]]:
    facts = []
    for label, pattern in FACT_PATTERNS:
        matches = list(pattern.finditer(text))
        # A salary in euros says more than the scale it may mention first.
        match = next((item for item in matches if "€" in item.group(0)), matches[0] if matches else None)
        if match:
            value = match.group(1) if pattern.groups else match.group(0)
            facts.append((label, " ".join(value.split()).rstrip(",-")))
    return facts


_CV_WORD = re.compile(r"[a-zà-ÿ][a-zà-ÿ0-9-]{4,}")


def profile_terms(markdown: str, extra: list[str] | None = None, common=None) -> list[str]:
    """Skills, job titles and their telling words from the CV, to find again in a vacancy.

    Skill lines are often short phrases that never occur word for word, so their words
    count on their own too. ``common`` takes a set of words and returns the ones too
    ordinary to mean anything (in many vacancies), which are left out.
    """
    phrases = list(extra or [])
    languages = set()
    for section in re.split(r"\n(?=## )", markdown or ""):
        head, _, body = section.partition("\n")
        if re.match(r"#+\s*(Talen|Languages)", head, re.I):
            # "Nederlands — moedertaal": the language is a demand vacancies name, however common.
            languages |= {match.lower() for match in re.findall(r"^[-*•\s]*([A-Za-zÀ-ÿ]{4,})", body, re.M)}
        elif re.match(r"#+\s*(Vaardigheden|Skills|Kernkwaliteiten|Competenties)", head, re.I):
            phrases += re.split(r"[,;\n•·|]", re.sub(r"\([^)]*\)", "", body))
        elif re.match(r"#+\s*(Werkervaring|Ervaring|Work experience|Experience)", head, re.I):
            # "### period — title | organisation, place": only the title says what you did.
            phrases += [line.split("|")[0].split(" — ")[-1] for line in re.findall(r"^###\s*(.+)$", body, re.M)]
    clean = {" ".join(phrase.strip(" -.*").split()) for phrase in phrases}
    words = {word for phrase in clean for word in _CV_WORD.findall(phrase.lower()) if word not in STOPWORDS}
    words -= set(common(words)) if common and words else set()
    terms = {term for term in clean if 3 < len(term) <= 40} | words | languages
    return sorted(terms, key=len, reverse=True)


def _pattern(terms: list[str]) -> re.Pattern | None:
    if not terms:
        return None
    # A plural or inflected ending still counts: "klantcontact" finds "klantcontacten".
    alternatives = "|".join(re.escape(term) for term in terms)
    return re.compile(r"(?<![\w-])(" + alternatives + r")(?:en|s|e|n)?(?![\w-])", re.I)


def marked(text: str, terms: list[str]) -> list[dict]:
    """The text in pieces: {text, mark}. mark: you (in your CV), key (facts and hard demands)."""
    spans: list[tuple[int, int, str]] = []
    you = _pattern(terms)
    if you:
        spans += [(match.start(), match.end(), "you") for match in you.finditer(text)]
    for _label, pattern in FACT_PATTERNS[:2]:
        spans += [(match.start(), match.end(), "key") for match in pattern.finditer(text)]
    spans += [(match.start(), match.end(), "key") for match in _IMPORTANT.finditer(text)]
    pieces, at = [], 0
    for start, end, mark in sorted(spans):
        if start < at:
            continue
        if start > at:
            pieces.append({"text": text[at:start], "mark": ""})
        pieces.append({"text": text[start:end], "mark": mark})
        at = end
    if at < len(text):
        pieces.append({"text": text[at:], "mark": ""})
    return pieces


def in_cv(text: str, terms: list[str]) -> bool:
    you = _pattern(terms)
    return bool(you and you.search(text))
