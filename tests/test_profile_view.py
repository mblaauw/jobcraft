"""Profile text as people read it: safe, formatted, without interview bookkeeping."""

from test_jobs_page import client  # noqa: F401

from app import profile_view


def test_markdown_becomes_readable_html_and_stays_safe():
    html = str(profile_view.render("**Label.** Tekst <b>los</b>\n\n1. Een\n2. Twee\n\n- Punt"))
    assert "<strong>Label.</strong>" in html and "&lt;b&gt;los&lt;/b&gt;" in html
    assert "<ol><li>Een</li><li>Twee</li></ol>" in html and "<ul><li>Punt</li></ul>" in html


def test_a_long_run_with_dots_becomes_its_own_list():
    html = str(profile_view.render("- Loopbaan: verkoper · gids · docent"))
    assert "Loopbaan:<ul><li>verkoper</li><li>gids</li><li>docent</li></ul>" in html


def test_interview_references_are_left_out():
    html = str(profile_view.render("- Buiten werken (vraag 8, 9 — love), niet zwaar."))
    assert "vraag" not in html and "Buiten werken, niet zwaar." in html


def test_a_teaser_skips_the_label_and_keeps_points_apart():
    text = "**Vastgelegde feiten.**\n\n- Opleiding: BBA\n- Loopbaan: a · b · c"
    assert profile_view.teaser(text) == "Opleiding: BBA; Loopbaan: a, b, c"
    assert profile_view.teaser("woord " * 80).endswith(" …")


def test_one_part_of_the_profile_can_be_changed_on_its_own(client):  # noqa: F811
    from app import profile, store

    profile.write_profile("## Kern\nOud feit.\n\n## Werkvoorkeuren\nBuiten werken.\n", "test")
    page = client.get("/profile?tab=cv").text
    assert 'action="/profile/sectie"' in page and "data-autogrow" in page
    answer = client.post("/profile/sectie", data={"heading": "Kern", "text": "Nieuw feit.\r\n- Een punt"}, follow_redirects=False)
    assert answer.status_code == 303
    sections = {item["heading"]: item["text"] for item in profile.split_sections(profile.read_profile())}
    assert sections["Kern"].strip() == "Nieuw feit.\n- Een punt"
    assert sections["Werkvoorkeuren"].strip() == "Buiten werken."
    assert any("aangepast: Kern" in (row["note"] or "") for row in store.list_profile_versions(5))
    assert client.post("/profile/sectie", data={"heading": "Bestaat niet", "text": "x"}).status_code == 404


PASTED = """# CV 2026 — vollediger tekst

Status: voorstel.

---

## Werkervaring
### JUN 2024 – HEDEN — Adviseur | Voorbeeldbedrijf, Utrecht

- Begeleidt klanten bij vragen over installatie,
  inclusief urgente storingen.

*Optionele zin.*

## Interesses
Natuur · Muziek · Culturen

# 1. Wat is toegevoegd

| Sectie | Bron |
|---|---|
| Over mij | dossier |

# 4. Nog te bevestigen

- Nummer +351 900 000 000?
"""


def test_a_pasted_cv_keeps_its_role_lines_tables_and_parts():
    from app import profile

    sections = profile.split_sections(PASTED)
    assert [item["heading"] for item in sections] == ["", "Werkervaring", "Interesses", "1. Wat is toegevoegd", "4. Nog te bevestigen"]
    assert "Status: voorstel." in sections[0]["text"] and "Wat is toegevoegd" not in sections[2]["text"]
    work = str(profile_view.render(sections[1]["text"]))
    assert '<h4 class="profile-sub">JUN 2024 – HEDEN — Adviseur | Voorbeeldbedrijf, Utrecht</h4>' in work
    assert "<li>Begeleidt klanten bij vragen over installatie, inclusief urgente storingen.</li>" in work
    assert "<em>Optionele zin.</em>" in work and "---" not in str(profile_view.render(sections[0]["text"]))
    table = str(profile_view.render(sections[3]["text"]))
    assert "<th>Sectie</th>" in table and "<td>dossier</td>" in table and "|" not in table


def test_writing_one_part_back_keeps_the_other_headings_as_they_were(tmp_path, monkeypatch):
    from app import profile

    monkeypatch.setattr(profile, "read_profile", lambda: PASTED)
    written = {}
    monkeypatch.setattr(profile, "write_profile", lambda text, note: written.update(text=text))
    profile.merge_sections({"Interesses": "Natuur"}, note="test")
    assert "\n# 1. Wat is toegevoegd\n" in written["text"] and "## Interesses\nNatuur" in written["text"]


def test_the_profile_warns_about_contact_details_notes_and_lost_parts():
    from app import profile_check

    before = "# Profiel\n\n## Kern\nFeit.\n\n## Zoekprofiel\nGroen.\n\n## Privé\n+31 6 12345678\n"
    titles = [item["title"] for item in profile_check.warnings(PASTED, before)]
    assert titles == [
        "Contactgegevens staan in je openbare profiel",
        "Werkaantekeningen worden gelezen als feiten over jou",
        "Bij je laatste wijziging zijn onderdelen weggevallen",
    ]
    assert profile_check.warnings(before, before) == []


def test_notes_move_out_of_the_profile_and_lost_parts_come_back():
    from app import profile, profile_check

    apart, moved = profile_check.notes_apart(PASTED)
    sections = {item["heading"]: item["text"] for item in profile.split_sections(apart)}
    assert "1. Wat is toegevoegd" not in sections and "Open vragen" not in sections
    assert "### 1. Wat is toegevoegd" in moved and "+351" in moved
    assert "+351" not in profile.public_excerpt(apart, 20000)
    assert profile_check.lost(apart, PASTED) == []
    before = "## Kern\nFeit.\n\n## Zoekprofiel\nGroen.\n"
    back = profile_check.lost_back(apart, before)
    headings = [item["heading"] for item in profile.split_sections(back)]
    assert headings[-2:] == ["Kern", "Zoekprofiel"] and "Werkervaring" in headings
    assert profile_check.warnings(back, before) == []


def test_the_one_click_fixes_save_a_new_version(client, monkeypatch):  # noqa: F811
    from app import profile

    profile.write_profile("## Kern\nFeit.\n\n## Zoekprofiel\nGroen.\n", "test")
    client.post("/profile/save", data={"content": PASTED})
    from app import private_notes

    page = client.get("/profile?tab=cv").text
    assert 'action="/profile/onderdelen-terug"' in page and 'action="/profile/aantekeningen-apart"' in page
    client.post("/profile/aantekeningen-apart")
    assert 'action="/profile/onderdelen-terug"' in client.get("/profile?tab=cv").text  # still known after a small fix
    client.post("/profile/onderdelen-terug")
    assert "Bij je laatste wijziging" not in client.get("/profile?tab=cv").text
    text = profile.read_profile()
    assert "## Kern\nFeit." in text and "Nog te bevestigen" not in text and "+351" not in text
    assert "### 4. Nog te bevestigen" in private_notes.load()["text"]
    assert "Nog te bevestigen" in client.get("/profile?tab=notities").text
