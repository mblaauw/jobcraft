---
name: career-gui-review
description: Beoordeel de interface van de career agent (dashboard, interview, bronnen, vacatures, vacaturedetail, profiel) op hiërarchie, consistentie, contrast, focus, lege en foutstatussen, responsiviteit en Nederlandse teksten; lever bevindingen met severity en bestandslocatie. Gebruik bij UI-werk, layoutklachten of een periodieke kwaliteitscontrole.
license: MIT
metadata:
  author: jobcraft
  version: "1.0.0"
  opencode/slash: "true"
---

# GUI-review career agent

## Kader

- Server-gerenderde Jinja-templates met één stylesheet: `app/static/app.css`.
- Bestaande tokens: `--ink`, `--muted`, `--line`, `--accent`, `--accent-ink`, `--panel`, `--ok`, `--warn`.
- Bestaande componenten: `.card`, `.banner`, `.button`, `.chip`, `.table`, `.steps`, `.score-badge`, `.travel`, `.vote`, `.filter-bar`.
- Geen framework of buildstap voorstellen; geen nieuwe visuele taal zonder reden.

## Checklist

1. Hiërarchie: één `h1` per pagina, `.sub` direct daaronder, secties als `.card`.
2. Consistentie: dezelfde actie ziet er hetzelfde uit (primaire knop, kleine knop, link).
3. Contrast: tekst minstens WCAG AA (4.5:1); let extra op `.muted` op de lichte panelen.
4. Focus: `:focus-visible` zichtbaar op knoppen, links, selects en checkboxes.
5. Statussen: leeg, bezig, fout en klaar hebben alle vier een zichtbare toestand.
6. Overloop: lange vacatureteksten, lange profielen en honderden rijen blijven netjes scrollen.
7. Responsief: breekpunten 780/820/980 px; tabellen mogen kolommen verbergen, niet onleesbaar krimpen.
8. Microcopy: Nederlands, kort, geen jargon; een foutmelding zegt wat de gebruiker moet doen.
9. Toegankelijkheid: elk formulier-element heeft een label; knoppen hebben tekst of `aria-label`; tabellen hebben koppen.
10. Eerlijkheid: geen verzonnen voortgang; genoemde kosten en wachttijden moeten kloppen.

## Werkwijze

- Lees de template plus de bijbehorende CSS-regels en haal de echte pagina op (curl) in plaats van te gokken.
- Rapporteer per bevinding: `bestand:regel — P0/P1/P2 — probleem — concrete fix`.
- P0 blokkeert gebruik, P1 is duidelijk hinderlijk, P2 is polish.
- Geen stijlvoorkeuren als bevinding; alleen waarneembare regels.
- Wijzig niet meer dan de bevindingen vragen; geen restyling van de hele app.
