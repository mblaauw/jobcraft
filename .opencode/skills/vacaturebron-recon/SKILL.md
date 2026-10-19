---
name: vacaturebron-recon
description: Beoordeel en configureer nieuwe vacaturebronnen voor de career agent - toets robots en voorwaarden, probeer feed, WordPress REST, JSON-LD, sitemap en publieke API's in vaste volgorde, kies een bestaand adapter-type en lever een verificatierapport. Gebruik wanneer een bron wordt toegevoegd, een bron stukloopt of het bronbestand wordt uitgebreid.
license: MIT
metadata:
  author: jobcraft
  version: "1.0.0"
  opencode/slash: "true"
---

# Vacaturebron-recon

Doel: nieuwe bronnen toevoegen op basis van bewijs, zonder te scrapen wat niet mag en
zonder eenmalige parsers te bouwen.

## 1. Toets eerst

- Lees de voorwaarden en `/robots.txt`. Alleen publieke feeds, publieke API's, sitemaps of pagina's die robots toestaat.
- Nooit inloggen, nooit anti-bot omzeilen, nooit rate limits ontwijken, nooit betaalmuren passeren.
- Sites die alleen via een zware JS-app werken: stel browser-import (import-by-URL) voor in plaats van een scraper.

## 2. Probeer in deze volgorde (goedkoop naar duur)

1. RSS/Atom: `/feed/`, `/rss`, `?rss=true`, of `<link type="application/rss+xml">` in de HTML.
2. WordPress REST: `/wp-json/wp/v2/types` en zoek een vacature-CPT; let op 401/404.
3. schema.org JobPosting: grep op `application/ld+json` en `JobPosting`, ook op detaillpagina's; sitemap naar detail werkt vaak beter dan de lijstpagina.
4. Sitemap: `/sitemap.xml` en sitemapindex naar childsitemaps; filter op vacature-URL's.
5. Publieke platform-API of ATS-host: bijvoorbeeld Exenzo/Greenjobs, `*.recruitee.com`, `*.homerun.co`, `*.personio.de`, Greenhouse, Lever.
6. HTML-listing: alleen als er server-gerenderde vacaturelinks zijn; configureer `link_pattern` en paginering.

## 3. Kies een bestaand adapter-type

`exenzo-json` · `wp-posts` · `html-fondsen` · `html-listing` · `sitemap-jsonld` · `rss`.
Past niets? Benoem welke nieuwe adapter nodig is en waarom; bouw geen eenmalige parser.

## 4. Kwaliteitseisen voor aanzetten

- Minimaal ongeveer 10 actuele vacatures, met locatie of coördinaten (of afleidbaar uit de slug).
- Stabiele URL's per vacature; geen URL's met sessie- of tracking-id's.
- Updatefrequentie past bij wekelijks scannen; anders uitzetten met een reden.

## 5. Beleefd scannen

- Maximaal ongeveer 1 verzoek per seconde per bron; paginacap per scan.
- Respecteer `429` en `Retry-After` en stop dan.
- Geen parallelle bursts en geen user-agent-spoofing verder dan een normale browser-UA.

## 6. Verificatierapport (verplicht)

Lever een tabel: bron · endpoint · bewijs (status, content-type, signaal) · gevonden
vacatures in de regio Amsterdam · beslissing (aan, uit, of nieuwe adapter nodig).
Leg de uitkomst vast in `seeds/sources.yaml` (notities) en in `PLAN.md`.
