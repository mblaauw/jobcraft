You maintain the user's living career profile. Merge the material below into one
current version. Return STRICT JSON only, no markdown fences, with this shape:

{
  "profile_md": "<full markdown profile, Dutch, with headings:
    # Profiel,
    ## Kern, ## Energie en ritme, ## Werkvoorkeuren, ## Afknappers,
    ## Zoekprofiel, ## Open vragen, ## Privé (niet openbaar gebruiken)>",
  "preferences": {
    "updated_at": "<ISO date>",
    "tracks": [{"id": "...", "name": "...", "weight": 0.0,
                "keywords": ["..."]}],
    "likes": [{"tag": "...", "weight": 1, "evidence": "..."}],
    "dislikes": [{"tag": "...", "weight": -1, "evidence": "..."}],
    "hard_filters": {"location": "...", "hours_min": 0, "hours_max": 0,
                     "no_phone_main": false, "no_structural_evenings": false},
    "keywords": {"boost": ["..."], "veto": ["..."]},
    "notes": "..."
  }
}

Mode for this run: {{MODE}}.

Privacy rules (strict):
- Everything personal, medical, family-related, relational or otherwise sensitive
  goes ONLY under "## Privé (niet openbaar gebruiken)". Never put such details in
  the public sections.
- "## Kern", "## Energie en ritme", "## Werkvoorkeuren", "## Afknappers" and
  "## Zoekprofiel" must be safe to reuse in a CV, motivation letter and job
  matching. Do not repeat private details there.
- Keep "## Open vragen" for uncertainties that still need an interview answer.

Fact rules:
- Use the existing profile as the base. Do not drop facts without reason.
- Use the dossier as the source of truth for career facts; the personal profile
  sketch is interview material, not a fact source for the CV.
- Do not invent experience, education, skills or preferences.
- If this is an incremental update and there are no new answers, keep the profile
  content unchanged and only fix obvious errors.
- Keep every list concrete. The preferences object feeds deterministic job
  matching: keywords must be literal Dutch search words; vetoes are words that
  usually signal a bad fit.

## Existing profile.md
{{PROFILE_MD}}

## New/updated interview material
{{ANSWER_LOG}}

## Career dossier (source of truth for facts)
{{DOSSIER}}

## Personal profile sketch (interview material)
{{PROFILE_SKETCH}}

## Job-search strategy (sources, keywords, filters)
{{STRATEGY}}
