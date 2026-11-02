"""Read a CV without AI: extract text, drop identity details, find roles and skills.

Nothing here calls a model. The result is shown to the user for correction, and only
the text they confirm enters the profile (and from there, `public_excerpt`).
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime

from docx import Document
from docx.table import Table
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from . import docx_safety, geo

MAX_BYTES = 5 * 1024 * 1024
MAX_PAGES = 15
MAX_CHARS = 30000

UNREADABLE = "We konden dit bestand niet lezen. Probeer een ander PDF- of DOCX-bestand."
NO_TEXT = "Er staat geen leesbare tekst in dit bestand, misschien is het een scan. Kies een ander bestand."

_SECTIONS = {
    "summary": ("profiel", "over mij", "samenvatting", "profile", "summary", "about me", "persoonlijk profiel"),
    "experience": ("werkervaring", "ervaring", "loopbaan", "work experience", "experience", "employment"),
    "skills": ("vaardigheden", "kernkwaliteiten", "competenties", "skills", "kennis", "expertise", "sterke punten"),
    "education": ("opleiding", "opleidingen", "education", "cursussen", "certificaten", "trainingen"),
    "languages": ("talen", "languages"),
}
_HEADINGS = {name: key for key, names in _SECTIONS.items() for name in names}
_IGNORED = ("referenties", "references", "hobby's", "hobbies", "interesses", "interests", "persoonsgegevens", "personal details", "contact", "contactgegevens")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL = re.compile(r"(?:https?://|www\.)\S+|\b(?:linkedin|github)\.com/\S+", re.I)
_PHONE = re.compile(r"(?<![\w-])(?:\+|00)?\d(?:[\s().-]*\d){8,13}(?!\w)")
_POSTCODE = re.compile(r"\b\d{4}\s?[A-Z]{2}\b(?:\s+[A-Z][\w'-]+)?")
_STREET = re.compile(r"\b[\w'-]*(?:straat|laan|weg|plein|gracht|kade|singel|dijk|hof|steeg)\s+\d+\w*\b", re.I)
_IDENTITY_LINE = re.compile(
    r"^\s*(?:geboortedatum|geboren|geboorteplaats|date of birth|bsn|burgerservicenummer|"
    r"nationaliteit|nationality|burgerlijke staat|marital status|geslacht|gender|adres|address|"
    r"telefoon|phone|e-?mail|woonplaats)\b.*$",
    re.I | re.M,
)
_MONTHS = (
    "januari|january|jan|februari|february|feb|maart|march|mrt|mar|april|apr|mei|may|juni|june|jun|"
    "juli|july|jul|augustus|august|aug|september|sept|sep|oktober|october|okt|oct|november|nov|december|dec"
)
# Before a year: a month name or number, as in "jan 2018" or "03-2015". Never just any word,
# or "Teamleider 2012" would lose its title.
_MONTH = rf"(?:\b(?:{_MONTHS})\b\.?\s*[/-]?\s*|\b(?:\d{{1,2}}\s*[/.-]\s*){{1,2}})?"
_RANGE_END = r"(?:[-–—]|t/m|tot|to)\s*" + _MONTH + r"((?:19|20)\d{2}|heden|nu|present|current|now)"
_YEAR_RANGE = re.compile(rf"{_MONTH}((?:19|20)\d{{2}})\s*{_RANGE_END}", re.I)
_YEAR = re.compile(rf"{_MONTH}\b(?:19|20)\d{{2}}\b(?:\s*{_RANGE_END})?", re.I)
_NOT_A_NAME = {"curriculum vitae", "cv", "c.v.", "resume", "résumé", "levensloop"}


def extract_text(raw: bytes, filename: str) -> str:
    """Plain text of an uploaded CV; raises ValueError with a message for the user."""
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        text = _pdf_text(raw)
    elif name.endswith(".docx"):
        text = _docx_text(raw)
    else:
        raise ValueError("Upload een PDF- of DOCX-bestand.")
    text = "\n".join(line.rstrip() for line in text.replace("\x00", "").splitlines())
    if len(re.sub(r"\s", "", text)) < 40:
        raise ValueError(NO_TEXT)
    return text[:MAX_CHARS]


def _pdf_text(raw: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise ValueError("Dit PDF-bestand is beveiligd. Kies een bestand zonder wachtwoord.")
        if len(reader.pages) > MAX_PAGES:
            raise ValueError(f"Een CV van meer dan {MAX_PAGES} pagina's kunnen we niet lezen.")
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ValueError as exc:
        if str(exc).endswith("."):
            raise
        raise ValueError(UNREADABLE) from None
    except (PyPdfError, OSError, KeyError, IndexError, TypeError, AttributeError, RecursionError):
        raise ValueError(UNREADABLE) from None


def _docx_text(raw: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if (
                len(archive.infolist()) > 1000
                or sum(item.file_size for item in archive.infolist()) > 20 * 1024 * 1024
            ):
                raise ValueError(UNREADABLE)
        docx_safety.validate_package(raw)
        document = Document(io.BytesIO(raw))
    except ValueError as exc:
        raise ValueError(str(exc) or UNREADABLE) from None
    except (zipfile.BadZipFile, KeyError, OSError, AttributeError):
        raise ValueError(UNREADABLE) from None
    # In document order: a table belongs under the heading above it, not under the last one.
    lines: list[str] = []
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            for row in block.rows:
                lines.extend(_row_lines(row))
        else:
            lines.append(block.text)
    return "\n".join(lines)


def _row_lines(row) -> list[str]:
    """One line per table row; a cell with several lines keeps them apart for the reader."""
    cells = list(dict.fromkeys(cell.text.strip() for cell in row.cells))
    if any("\n" in cell for cell in cells):
        return [line for cell in cells for line in cell.splitlines()]
    return [" | ".join(cells)]


def _heading_key(line: str) -> str | None:
    label = re.sub(r"[\s:_•\-–—]+$|^[\s•\-–—]+", "", line).casefold()
    if not label or len(label) > 30:
        return None
    if label in _IGNORED:
        return "ignored"
    return _HEADINGS.get(label)


def split_cv(text: str) -> tuple[list[str], dict[str, list[str]]]:
    """Header lines (identity block) and body lines per recognised section."""
    header: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        key = _heading_key(line)
        if key:
            current = key
            sections.setdefault(key, [])
        elif current is None:
            header.append(line)
        elif line.strip():
            sections[current].append(line.strip())
    sections.pop("ignored", None)
    return header, sections


def _names_in(header: list[str], display_name: str) -> list[str]:
    names = [display_name.strip()] if display_name.strip() else []
    first = next(
        (line.strip() for line in header if line.strip() and line.strip().casefold() not in _NOT_A_NAME),
        "",
    )
    if first and len(first.split()) <= 5 and not re.search(r"\d|@", first):
        names.append(first)
    return names


def redact(text: str, names: list[str] | None = None) -> str:
    """Remove contact and identity details; deliberately errs on the side of removing."""
    text = _IDENTITY_LINE.sub("", text)
    for pattern in (_EMAIL, _URL, _POSTCODE, _STREET):
        text = pattern.sub("", text)
    text = _PHONE.sub(lambda match: "" if not _YEAR_RANGE.search(match.group(0)) else match.group(0), text)
    for name in names or []:
        text = re.sub(re.escape(name), "", text, flags=re.I)
        parts = [part for part in name.split() if len(part) > 2 and part[0].isupper()]
        for part in parts[-1:]:  # a surname alone also identifies someone
            text = re.sub(rf"\b{re.escape(part)}\b", "", text)
    return "\n".join(re.sub(r"[ \t]{2,}", " ", line).strip(" |,·•") for line in text.splitlines())


def _clean_role(line: str) -> str:
    line = _YEAR.sub(" ", line)
    line = re.split(r"\s+(?:bij|at|voor)\s+|\s*[|·•,–—]\s*|\s+-\s+", line.strip(" |·•-–—\t"), maxsplit=1)[0]
    return re.sub(r"\s+", " ", line).strip(" :-–—|·•")


def _roles(experience: list[str]) -> list[str]:
    roles: list[str] = []
    for index, line in enumerate(experience):
        if not _YEAR_RANGE.search(line):
            continue
        role = _clean_role(line)
        if not role and index + 1 < len(experience):  # dates on their own line, title below
            role = _clean_role(experience[index + 1])
        if role and 2 < len(role) <= 60 and len(role.split()) <= 6 and role.casefold() not in (r.casefold() for r in roles):
            roles.append(role)
    return roles[:5]


def _skills(lines: list[str]) -> list[str]:
    skills: list[str] = []
    for line in lines:
        for item in re.split(r"[,;•·|/]|\s{2,}|\s-\s", line):
            item = item.strip(" .-–—\t")
            if 1 < len(item) <= 40 and len(item.split()) <= 4 and item.casefold() not in (s.casefold() for s in skills):
                skills.append(item)
    return skills[:15]


def _seniority(experience: list[str], roles: list[str], now: int | None = None) -> str:
    title = " ".join(roles[:1]).casefold()
    if re.search(r"\b(?:senior|lead|principal|hoofd|manager)\b", title):
        return "senior"
    if re.search(r"\b(?:junior|trainee|stagiair)\b", title):
        return "junior"
    now = now or datetime.now(UTC).year
    starts = [int(match.group(1)) for line in experience for match in _YEAR_RANGE.finditer(line)]
    if not starts:
        return ""
    years = now - min(starts)
    return "senior" if years >= 8 else "medior" if years >= 3 else "junior"


def read_cv(text: str, display_name: str = "") -> dict:
    """What the review step shows: suggestions plus identity-free profile text."""
    header, sections = split_cv(text)
    names = _names_in(header, display_name)
    # Without the name: "de Vries" must not suggest the village of Vries.
    place = geo._find_place(" ".join(line for line in header if line.strip() not in names)) or ""
    clean = {key: redact("\n".join(lines), names).strip() for key, lines in sections.items()}
    clean = {key: re.sub(r"\n{3,}", "\n\n", value) for key, value in clean.items() if value}
    if not clean:  # no recognisable headings: keep the body, without the identity block
        clean = {"summary": redact("\n".join(header[3:]), names).strip()}
    experience = clean.get("experience", "").splitlines()
    roles = _roles(experience)
    return {
        "roles": roles,
        "skills": _skills(clean.get("skills", "").splitlines()),
        "seniority": _seniority(experience, roles),
        "place": geo.PLACE_LABELS.get(place, ""),
        "sections": clean,
    }


SECTION_TITLES = {
    "summary": "Over mij",
    "experience": "Werkervaring",
    "skills": "Vaardigheden",
    "education": "Opleiding",
    "languages": "Talen",
}


def profile_markdown(sections: dict[str, str]) -> dict[str, str]:
    """Section heading -> confirmed text, ready to merge into the profile."""
    return {SECTION_TITLES[key]: value.strip() for key, value in sections.items() if value.strip() and key in SECTION_TITLES}
