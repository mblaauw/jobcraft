"""The shape of a tailored CV and letter, and the checks every version must pass.

The checks run without AI. A model can write something believable that was never true, so
numbers and names are compared with what the person and the vacancy actually said.
"""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field

LETTER_MIN_WORDS = 120
LETTER_MAX_WORDS = 330

BANNED = (
    "hierbij solliciteer",
    "met veel interesse",
    "met grote interesse",
    "gedreven",
    "passie",
    "nieuwe uitdaging",
    "teamplayer",
    "duizendpoot",
    "ik ben ervan overtuigd",
    "resultaatgericht",
    "in een wereld waarin",
    "naadloos",
    "dynamische",
    "hands-on mentaliteit",
    "i am writing to",
    "passionate",
)
# Capitalised in the middle of a sentence without being a name someone could invent.
_COMMON_CAPITALS = {
    "ik", "nederlands", "engels", "duits", "frans", "januari", "februari", "maart", "april",
    "mei", "juni", "juli", "augustus", "september", "oktober", "november", "december",
    "maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag",
    "nederland", "europese", "europa", "i", "cv", "ai",
}


class CvRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(min_length=1, max_length=120)
    organisation: str = Field(max_length=120)
    period: str = Field(max_length=60)
    bullets: list[str] = Field(max_length=7)


class CvEducation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    organisation: str = Field(max_length=120)
    period: str = Field(max_length=60)


class CvContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headline: str = Field(min_length=2, max_length=110)
    profile: str = Field(min_length=60, max_length=800)
    highlights: list[str] = Field(min_length=2, max_length=5)
    experience: list[CvRole] = Field(max_length=12)
    education: list[CvEducation] = Field(max_length=8)
    skills: list[str] = Field(max_length=20)
    languages: list[str] = Field(max_length=8)


class LetterContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    salutation: str = Field(min_length=2, max_length=90)
    paragraphs: list[str] = Field(min_length=2, max_length=6)


class CriticReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: int = Field(ge=0, le=100)
    first_impression: str
    strongest: str
    unsupported: list[str] = Field(max_length=10)
    fixes: list[str] = Field(max_length=8)


def is_structured_cv(content: dict) -> bool:
    return "experience" in content


def is_structured_letter(content: dict) -> bool:
    return "salutation" in content


def cv_lines(content: dict) -> list[str]:
    lines = [content.get("headline", ""), content.get("profile", ""), *content.get("highlights", [])]
    for role in content.get("experience", []):
        lines.extend([f"{role['role']}, {role['organisation']}, {role['period']}", *role["bullets"]])
    for item in content.get("education", []):
        lines.append(f"{item['title']}, {item['organisation']}, {item['period']}")
    lines.extend(content.get("skills", []))
    lines.extend(content.get("languages", []))
    return [line for line in lines if line]


def letter_lines(content: dict) -> list[str]:
    return [content.get("salutation", ""), *content.get("paragraphs", [])]


def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


def _numbers(text: str) -> set[str]:
    return {re.sub(r"[.,](?=\d{3}\b)", "", item) for item in re.findall(r"\d+(?:[.,]\d+)*", text)}


def _names(text: str) -> set[str]:
    """Capitalised words that do not open a sentence: in Dutch, nearly always names."""
    found = set()
    for sentence in re.split(r"(?<=[.!?:;])\s+|\n", text):
        words = re.findall(r"[^\W\d_][\w'’&-]*", sentence)
        for word in words[1:]:
            if word[0].isupper() and _plain(word) not in _COMMON_CAPITALS and len(word) > 1:
                found.add(word)
    return found


def unsupported_facts(lines: list[str], source: str) -> list[str]:
    """Numbers and names in the text that appear nowhere in what we were given."""
    plain_source = _plain(source)
    known_numbers = _numbers(source)
    problems = []
    for number in sorted(_numbers(" ".join(lines))):
        if number not in known_numbers:
            problems.append(f"Het getal {number} staat niet in je gegevens of de vacature.")
    for name in sorted(_names("\n".join(lines))):
        if _plain(name) not in plain_source:
            problems.append(f"De naam of term \"{name}\" staat niet in je gegevens of de vacature.")
    return problems


def roles_changed(content: dict, source: str) -> list[str]:
    """Employers and titles must be copied, never polished into something else."""
    plain_source = _plain(source)
    problems = []
    for role in content.get("experience", []):
        for field in ("role", "organisation"):
            value = (role.get(field) or "").strip()
            if value and _plain(value) not in plain_source:
                problems.append(f"\"{value}\" staat niet zo in je cv.")
    return problems


def style_problems(lines: list[str]) -> list[str]:
    plain = _plain(" ".join(lines))
    problems = [f"Cliché: \"{phrase}\"." for phrase in BANNED if phrase in plain]
    if "—" in " ".join(lines):
        problems.append("Gedachtestreepjes lezen als AI; schrijf gewone zinnen.")
    return problems


def letter_length_problem(content: dict) -> str:
    words = len(" ".join(content.get("paragraphs", [])).split())
    if words < LETTER_MIN_WORDS:
        return f"De brief is met {words} woorden te kort; mik op 170 tot 270 woorden."
    if words > LETTER_MAX_WORDS:
        return f"De brief is met {words} woorden te lang; mik op 170 tot 270 woorden."
    return ""
