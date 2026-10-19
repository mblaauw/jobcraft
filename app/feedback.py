"""Deterministic learning from the user's up/down votes.

The vote buttons write to the ``jobs.vote`` column; this module turns those
votes into a small, transparent feedback profile:

- keyword sets that recur in upvoted versus downvoted jobs (contrastive, so a
  word that shows up in both classes is ignored);
- companies and sources the user consistently likes or dislikes.

The rule pre-score and the matcher prompt both consume this file, so every new
vote changes future ranking immediately — no extra LLM call needed. The file is
human-readable and can be inspected or reset (delete it and vote again).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

from . import config, store
from .util import now_iso

FEEDBACK_FILE = config.PROFILE_DIR / "feedback.json"

STOPWORDS = {
    "de",
    "het",
    "een",
    "en",
    "van",
    "voor",
    "met",
    "op",
    "in",
    "te",
    "dat",
    "die",
    "is",
    "zijn",
    "als",
    "bij",
    "of",
    "je",
    "jij",
    "wij",
    "we",
    "ons",
    "onze",
    "niet",
    "ook",
    "maar",
    "aan",
    "om",
    "er",
    "dan",
    "nog",
    "wel",
    "kan",
    "kunnen",
    "worden",
    "wordt",
    "heeft",
    "hebben",
    "heb",
    "zal",
    "zullen",
    "per",
    "tot",
    "door",
    "over",
    "naar",
    "uit",
    "deze",
    "dit",
    "meer",
    "alle",
    "hun",
    "zich",
    "zijn",
    "gaan",
    "gaat",
    "maak",
    "maakt",
    "krijg",
    "krijgt",
    "werk",
    "werken",
    "werkt",
    "functie",
    "vacature",
    "baan",
    "soliciteer",
    "solliciteren",
    "sollicitatie",
    "organisatie",
    "team",
    "binnen",
    "buiten",
    "nieuwe",
    "nieuw",
    "goede",
    "goed",
    "graag",
    "kunt",
    "via",
    "meer",
    "jaar",
    "uren",
    "uur",
    "uurcontract",
    "dienstverband",
    "fulltime",
    "parttime",
    "like",
    "plus",
    "over",
    "onder",
    "tussen",
    "tijdens",
    "zowel",
    "daar",
    "waar",
    "hoe",
    "wie",
    "wat",
    "waarom",
    "want",
    "omdat",
    "zodat",
    "word",
    "ben",
    "bent",
    "hebt",
    "heeft",
    "hun",
    "haar",
    "hem",
    "hen",
    "ons",
    "jullie",
    "the",
    "and",
    "for",
    "with",
    "you",
    "your",
    "our",
    "are",
    "will",
    "that",
    "this",
    "from",
    "have",
    "has",
    "job",
    "jobs",
    "work",
    "team",
}


STOPWORDS |= {
    "geen",
    "hetzelfde",
    "staat",
    "maken",
    "maakt",
    "iemand",
    "eind",
    "slag",
    "iedereen",
    "alleen",
    "altijd",
    "andere",
    "beste",
    "beter",
    "daarom",
    "daarnaast",
    "dezelfde",
    "dus",
    "echter",
    "eigen",
    "enkele",
    "genoeg",
    "hele",
    "hier",
    "iets",
    "jouw",
    "keer",
    "kleine",
    "laatste",
    "later",
    "liever",
    "mee",
    "meest",
    "moeten",
    "moment",
    "naast",
    "nodig",
    "omgeving",
    "opnieuw",
    "plaats",
    "precies",
    "samen",
    "sommige",
    "steeds",
    "tegen",
    "terwijl",
    "toch",
    "uiteraard",
    "vanaf",
    "verder",
    "verschillende",
    "veel",
    "weinig",
    "weken",
    "zelf",
    "zelfs",
    "zou",
    "zonder",
    "kennis",
    "ervaring",
    "contact",
    "among",
    "also",
}


def _tokens(row) -> set[str]:
    text = f"{row['title'] or ''} {row['description'] or ''}".lower()
    return {word for word in re.findall(r"[a-zà-ÿ]{4,}", text) if word not in STOPWORDS}


def _document_frequency() -> tuple[Counter, int]:
    """How many jobs contain each token: used to drop boilerplate keywords."""
    corpus: Counter = Counter()
    with store.connect() as con:
        rows = con.execute("SELECT title, description FROM jobs").fetchall()
    total = 0
    for row in rows:
        tokens = _tokens(row)
        if not tokens:
            continue
        total += 1
        for token in tokens:
            corpus[token] += 1
    return corpus, total


def _contrast(
    counter: Counter, other: Counter, corpus: Counter, total: int, limit: int = 40
) -> list[dict]:
    items: list[dict] = []
    for word, count in counter.most_common(300):
        if other.get(word, 0) >= count:
            continue
        # A word that appears in a large share of all jobs says more about job
        # ads in general than about the user's preference.
        if total and corpus.get(word, 0) / total > 0.35:
            continue
        items.append({"keyword": word, "count": count})
        if len(items) >= limit:
            break
    return items


def rebuild() -> dict:
    votes = store.list_votes()
    liked: Counter = Counter()
    disliked: Counter = Counter()
    for row in votes["up"]:
        liked.update(_tokens(row))
    for row in votes["down"]:
        disliked.update(_tokens(row))
    corpus, total = _document_frequency()

    liked_sources: Counter = Counter(row["source_id"] or "?" for row in votes["up"])
    disliked_sources: Counter = Counter(row["source_id"] or "?" for row in votes["down"])
    payload = {
        "updated_at": now_iso(),
        "counts": {"up": len(votes["up"]), "down": len(votes["down"])},
        "liked_keywords": _contrast(liked, disliked, corpus, total),
        "disliked_keywords": _contrast(disliked, liked, corpus, total),
        "liked_companies": sorted({row["company"] for row in votes["up"] if row["company"]})[:25],
        "disliked_companies": sorted({row["company"] for row in votes["down"] if row["company"]})[
            :25
        ],
        "liked_titles": [row["title"] for row in votes["up"][:15] if row["title"]],
        "disliked_titles": [row["title"] for row in votes["down"][:15] if row["title"]],
        "liked_sources": dict(liked_sources.most_common(8)),
        "disliked_sources": dict(disliked_sources.most_common(8)),
    }
    config.user_path(FEEDBACK_FILE).parent.mkdir(parents=True, exist_ok=True)
    config.user_path(FEEDBACK_FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def load() -> dict:
    if not config.user_path(FEEDBACK_FILE).exists():
        return rebuild()
    try:
        return json.loads(config.user_path(FEEDBACK_FILE).read_text())
    except ValueError:
        return rebuild()


def signature() -> str:
    data = load()
    basis = {
        "liked": [item["keyword"] for item in data.get("liked_keywords", [])],
        "disliked": [item["keyword"] for item in data.get("disliked_keywords", [])],
        "counts": data.get("counts"),
        "companies": [data.get("liked_companies"), data.get("disliked_companies")],
    }
    return hashlib.sha256(
        json.dumps(basis, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:10]


def rule_adjust(
    text: str, company: str | None, source_id: str | None, data: dict | None = None
) -> tuple[float, list[str]]:
    data = data if data is not None else {}
    if not data.get("counts", {}).get("up") and not data.get("counts", {}).get("down"):
        return 0.0, []
    low = text.lower()
    delta = 0.0
    notes: list[str] = []

    liked_hits = [
        item["keyword"] for item in data.get("liked_keywords", []) if item["keyword"] in low
    ]
    if liked_hits:
        delta += min(6.0, 0.8 * len(liked_hits))
        notes.append("feedback+: " + ", ".join(liked_hits[:4]))
    disliked_hits = [
        item["keyword"] for item in data.get("disliked_keywords", []) if item["keyword"] in low
    ]
    if disliked_hits:
        delta -= min(8.0, 1.2 * len(disliked_hits))
        notes.append("feedback-: " + ", ".join(disliked_hits[:4]))

    if company:
        company_low = company.lower()
        if company_low in {name.lower() for name in data.get("liked_companies", [])}:
            delta += 4.0
            notes.append("eerdere upvote voor organisatie")
        elif company_low in {name.lower() for name in data.get("disliked_companies", [])}:
            delta -= 6.0
            notes.append("eerdere downvote voor organisatie")

    if source_id:
        liked_sources = data.get("liked_sources", {})
        disliked_sources = data.get("disliked_sources", {})
        if disliked_sources.get(source_id, 0) > liked_sources.get(source_id, 0):
            delta -= 2.0
        elif liked_sources.get(source_id, 0) > disliked_sources.get(source_id, 0):
            delta += 2.0

    return round(delta, 1), notes


def prompt_block(data: dict | None = None, limit: int = 10) -> str:
    data = data if data is not None else load()
    counts = data.get("counts", {})
    if not counts.get("up") and not counts.get("down"):
        return ""
    lines = ["## Feedback van de gebruiker (leer hiervan)"]
    liked = [item["keyword"] for item in data.get("liked_keywords", [])[:limit]]
    disliked = [item["keyword"] for item in data.get("disliked_keywords", [])[:limit]]
    if liked:
        lines.append("Terugkerende woorden in omhoog gestemde vacatures: " + ", ".join(liked))
    if disliked:
        lines.append("Terugkerende woorden in omlaag gestemde vacatures: " + ", ".join(disliked))
    if data.get("liked_titles"):
        lines.append("Gewaardeerde vacatures: " + " | ".join(data["liked_titles"][:6]))
    if data.get("disliked_titles"):
        lines.append("Afgewezen vacatures: " + " | ".join(data["disliked_titles"][:6]))
    lines.append(
        "Weeg vacatures die sterk lijken op de gewaardeerde voorbeelden hoger en op de "
        "afgewezen voorbeelden lager. Noem 'feedback' in je reden als dit meeweegt."
    )
    return "\n".join(lines)
