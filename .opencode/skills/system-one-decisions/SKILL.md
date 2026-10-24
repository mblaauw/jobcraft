---
name: system-one-decisions
description: Gebruik Jev (TypeSafe System One via Runware) voor snelle getypeerde beslissingen (noul/choice/score) met gekalibreerde confidence, inclusief het exacte API-contract, de schema-valkuilen en het confidence-routerpatroon. Gebruik bij matching, classificatie, routering of elk besluit dat geen prose nodig heeft.
license: MIT
metadata:
  author: jobcraft
  version: "1.0.0"
  opencode/slash: "true"
---

# System One-beslissingen (Jev via Runware)

Jev geeft **getypeerde antwoorden** (geen tekst): `noul` (kans op ja), `choice`
(label + verdeling) en `score` (gewogen waarde over een rubric), elk met
confidence. Snel (~0,5 s) en veel goedkoper dan een LLM-verdict — gebruik het als
poortwachter en laat de LLM alleen twijfelgevallen doen.

## Contract (geverifieerd 2026-10-18)

```http
POST https://api.runware.ai/v1/decisions
Authorization: Bearer $RUNWARE_API_KEY
Content-Type: application/json
```

```json
{
  "model": "typesafe:jev@1",
  "state": "…vrije tekst of JSON…",
  "questions": {
    "category": {
      "type": "choice",
      "instructions": "Wat voor functie is dit?",
      "criteria": { "techniek": "installatie, onderhoud", "administratie": "planning, administratie" }
    },
    "fit": {
      "type": "score",
      "instructions": "Hoe goed past dit?",
      "criteria": ["slecht", "matig", "redelijk", "goed", "uitstekend"]
    },
    "phone_main": {
      "type": "noul",
      "instructions": "Is telefonie de hoofdmoot?",
      "criteria": { "true": "bellen is het grootste deel", "false": "nauwelijks telefoon" }
    }
  }
}
```

## Schema-valkuilen (dit kostte een 400)

- `score.criteria` is een **geordende array** van 2–10 niveaus, géén map.
- `choice.criteria` is een **map** optie → beschrijving (of `null`).
- `noul` heeft optionele `criteria` met `true`/`false`; de respons is alleen een
  kans, **geen confidence** — gebruik `max(p, 1-p)`.
- Response: `answers.<id> = {type, choice|noul|score, probabilities, confidence}`;
  `score` heeft ook `legend`. Tokens staan in `usage`.

## Patroon: confidence-router

1. Stel 5–10 noul-vragen + 1 choice + 1 score samen.
2. `confidence = min(choice.confidence, score.confidence, max(p,1-p) per noul)`.
3. `confidence >= drempel` (hier 0,75) → beslissing accepteren, **geen LLM**.
4. Lager → escalate naar de LLM (System 2) en log het geval als label.
5. Cache de getypeerde antwoorden op `hash(question_version + content_hash +
   publiek_profiel)`; **nooit op feedback/votes** — anders betaal je bij elke stem.

## Kosten en meten

- Eén call kost ~2,1k input- en ~270 output-tokens (state = vacaturetekst +
  compact profiel). Houd de state kort: ~3.000 tekens vacature is genoeg.
- Runware rekent via je eigen key; exacte Jev-prijs staat op de Runware-pagina.
  Meet na ~20 calls de dashboardkosten en noteer ze in de projectnotities.
- `typesafe:jev@1` is de werkende model-id; `runware:laya@1` bestaat ook maar gaf
  op 2026-10-18 een 429 (upstream rate limit).

## Projectintegratie

- Client: `app/systemone.py` (vragen, `decide`, `signals_from_answers`).
- Router/ranking: `app/ranking.py` (confidence-drempel, vrije herweging bij votes).
- Meten: `uv run python scripts/matching_quality.py` (overlap en correlatie met
  LLM-verdicts vóór je gewichten wijzigt).
