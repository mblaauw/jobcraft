"""The plan behind one application: what they want, your proof, and the story to tell."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from . import employer_research, profile, standards, store
from .llm_client import LLMError
from .util import now_iso, parse_json_object

STORIES = "doc_stories"
MAX_STORIES = 20
# Constraints that strict structured output rejects on some models; they are checked here.
_LOOSE = {"title", "default", "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum"}


class EmployerFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact: str
    source_url: str


class Research(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Literal["nl", "en"]
    formality: Literal["je", "u"]
    contact_person: str
    role_in_one_line: str
    real_problem: str
    must_haves: list[str]
    nice_to_haves: list[str]
    keywords: list[str]
    culture_signals: list[str]
    employer_facts: list[EmployerFact]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    need: str
    proof: str
    strength: Literal["sterk", "deels", "ontbreekt"]


class Angle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    story: str


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    question: str
    why: str


class Gap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gap: str
    how_to_handle: str


class Strategy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fit_summary: str
    evidence: list[Evidence]
    angles: list[Angle]
    recommended_angle: str
    questions: list[Question]
    gaps: list[Gap]


def schema_for(model: type[BaseModel]) -> dict:
    def clean(node, *, names: bool = False):
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        if names:  # Keys here are field names, and a field may well be called "title".
            return {key: clean(value) for key, value in node.items()}
        return {
            key: clean(value, names=key in ("properties", "$defs"))
            for key, value in node.items()
            if key not in _LOOSE
        }

    return clean(model.model_json_schema())


def record_usage(usage: dict, agent: str) -> None:
    store.record_usage(
        None,
        "documents",
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
        float(usage.get("cost_usd") or 0.0),
        agent=agent,
    )


def call_json(mgr, prompt: str, *, agent: str, model: type[BaseModel], ref: str) -> dict:
    """One model call that must come back valid; a second try is told what was wrong."""
    schema: dict | None = schema_for(model)
    problem = ""
    for attempt in range(3):
        request = prompt + (f"\n\nJe vorige antwoord was ongeldig: {problem} Geef het hele antwoord opnieuw." if problem else "")
        try:
            text, usage = mgr.generate(request, agent=agent, schema=schema, ref=ref)
        except LLMError as exc:
            # Not every model takes a strict schema; the prompt itself also states the shape.
            if schema is not None and "HTTP 4" in str(exc) and attempt == 0:
                schema = None
                continue
            raise
        record_usage(usage, agent)
        try:
            return model.model_validate(parse_json_object(text)).model_dump()
        except (ValueError, ValidationError) as exc:
            problem = str(exc)[:600]
    raise ValueError(f"{agent}: geen geldig antwoord: {problem}")


# ---------------------------------------------------------------- stored per job


def _key(job_id: int) -> str:
    return f"plan:{job_id}"


def load(job_id: int) -> dict | None:
    raw = store.meta_get(_key(job_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def save(job_id: int, plan: dict) -> None:
    store.meta_set(_key(job_id), json.dumps(plan, ensure_ascii=False))


def choose(job_id: int, angle: str, answers: dict[str, str], note: str) -> dict:
    """Keep what someone chose and told us; their stories also serve later applications."""
    plan = load(job_id) or {}
    known = {item["id"]: item for item in plan.get("strategy", {}).get("angles", [])}
    if angle in known:
        plan["angle"] = angle
    questions = {item["id"]: item["question"] for item in plan.get("strategy", {}).get("questions", [])}
    kept = {key: value.strip()[:1500] for key, value in answers.items() if key in questions and value.strip()}
    plan["answers"] = kept
    plan["note"] = note.strip()[:2000]
    save(job_id, plan)
    for key, value in kept.items():
        add_story(questions[key], value)
    return plan


# ---------------------------------------------------------------- stories


def stories() -> list[dict]:
    try:
        data = json.loads(store.meta_get(STORIES) or "[]")
    except ValueError:
        return []
    return [item for item in data if isinstance(item, dict)]


def add_story(question: str, answer: str) -> None:
    kept = [item for item in stories() if item.get("answer") != answer]
    kept.append({"question": question, "answer": answer})
    store.meta_set(STORIES, json.dumps(kept[-MAX_STORIES:], ensure_ascii=False))


def remove_story(index: int) -> None:
    kept = stories()
    if 0 <= index < len(kept):
        kept.pop(index)
        store.meta_set(STORIES, json.dumps(kept, ensure_ascii=False))


def _stories_text() -> str:
    return "\n".join(f"- {item['question']}: {item['answer']}" for item in stories())[:5000]


def person_source(plan: dict | None = None) -> str:
    """Everything a document may say about the person. Name and contact are never in here."""
    parts = [profile.read_document_excerpt(12000)]
    stories_text = _stories_text()
    if stories_text:
        parts.append("## Aanvullingen van de gebruiker\n" + stories_text)
    if plan and plan.get("note"):
        parts.append("## Toelichting van de gebruiker voor deze sollicitatie\n" + plan["note"])
    return "\n\n".join(part for part in parts if part)


def employer_source(job: dict, plan: dict | None) -> str:
    pages = (plan or {}).get("pages") or []
    return "\n\n".join(
        [
            f"{job.get('title') or ''} bij {job.get('company') or ''}",
            str(job.get("description") or ""),
            *(page.get("text") or "" for page in pages),
        ]
    )


# ---------------------------------------------------------------- making the plan


def research_prompt(job: dict, pages: list[dict]) -> str:
    analysis = standards.standards_block(("vacature-analyse",), heading="## Zo lees je een vacature")
    found = "\n\n".join(f"### {page['url']}\n{page['text']}" for page in pages) or "(niets gevonden)"
    return (
        f"## Vacature\n{job.get('title')} bij {job.get('company') or 'onbekende organisatie'}\n"
        f"Plaats: {job.get('location') or 'onbekend'}\n{str(job.get('description') or '')[:9000]}\n\n"
        f"## Openbare pagina's over de werkgever\n{found}\n\n{analysis}\n\n"
        "Geef JSON met: language, formality, contact_person, role_in_one_line, real_problem, "
        "must_haves (hoogstens 6), nice_to_haves (hoogstens 4), keywords (5 tot 12), "
        "culture_signals (hoogstens 4), employer_facts (hoogstens 5, elk {fact, source_url})."
    )


def strategy_prompt(job: dict, research: dict) -> str:
    return (
        f"## Vacature\n{job.get('title')} bij {job.get('company') or 'onbekende organisatie'}\n\n"
        f"## Analyse van vacature en werkgever\n{json.dumps(research, ensure_ascii=False)}\n\n"
        f"## Wat we van de gebruiker weten\n{person_source()}\n\n"
        "Geef JSON met: fit_summary (twee zinnen, eerlijk), evidence (elke belangrijke eis met "
        "{need, proof, strength}), angles (precies 3, elk {id, title, story}; id is a, b of c; "
        "story vertelt in twee zinnen het verhaal van deze invalshoek), recommended_angle, "
        "questions (0 tot 3, elk {id, question, why}; id is q1, q2 of q3), "
        "gaps (hoogstens 3, elk {gap, how_to_handle})."
    )


def make(mgr, job: dict, emit=lambda stage: None) -> dict:
    ref = f"job:{job['id']}"
    emit("research")
    pages = employer_research.gather(job)
    research = call_json(mgr, research_prompt(job, pages), agent="doc_researcher", model=Research, ref=ref)
    research["employer_facts"] = _sourced_facts(research["employer_facts"], pages)
    emit("strategy")
    strategy = call_json(mgr, strategy_prompt(job, research), agent="doc_strategist", model=Strategy, ref=ref)
    if strategy["recommended_angle"] not in {item["id"] for item in strategy["angles"]} and strategy["angles"]:
        strategy["recommended_angle"] = strategy["angles"][0]["id"]
    earlier = load(job["id"]) or {}
    plan = {
        "created_at": now_iso(),
        "pages": pages,
        "research": research,
        "strategy": strategy,
        "angle": strategy["recommended_angle"],
        "answers": {},
        "note": earlier.get("note", ""),
    }
    save(job["id"], plan)
    return plan


def _sourced_facts(facts: list[dict], pages: list[dict]) -> list[dict]:
    """Only an address we really read counts as a source; a fact without one is dropped."""
    known = {page["url"] for page in pages}
    return [fact for fact in facts if fact.get("source_url") in known]


def writing_brief(plan: dict) -> str:
    """The part of the plan a writer works from."""
    research = plan.get("research") or {}
    strategy = plan.get("strategy") or {}
    angle = next(
        (item for item in strategy.get("angles", []) if item["id"] == plan.get("angle")), None
    )
    questions = {item["id"]: item["question"] for item in strategy.get("questions", [])}
    answers = [f"- {questions.get(key, key)}: {value}" for key, value in (plan.get("answers") or {}).items()]
    return "\n".join(
        part
        for part in (
            f"Taal: {research.get('language', 'nl')}. Aanspreekvorm: {research.get('formality', 'je')}.",
            f"Contactpersoon: {research.get('contact_person')}." if research.get("contact_person") else "",
            f"Wat deze rol moet oplossen: {research.get('real_problem', '')}",
            "Eisen: " + "; ".join(research.get("must_haves") or []),
            "Kernwoorden: " + ", ".join(research.get("keywords") or []),
            "Sfeer: " + "; ".join(research.get("culture_signals") or []),
            "Feiten over de werkgever: "
            + "; ".join(f"{item['fact']}" for item in research.get("employer_facts") or []),
            "Bewijs per eis: " + json.dumps(strategy.get("evidence") or [], ensure_ascii=False),
            f"Gekozen invalshoek: {angle['title']}. {angle['story']}" if angle else "",
            "Nieuwe antwoorden van de gebruiker:\n" + "\n".join(answers) if answers else "",
        )
        if part and not re.fullmatch(r"[^:]*:\s*", part)
    )
