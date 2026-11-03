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
    page = client.get("/profile").text
    assert 'action="/profile/sectie"' in page and "data-autogrow" in page
    answer = client.post("/profile/sectie", data={"heading": "Kern", "text": "Nieuw feit.\r\n- Een punt"}, follow_redirects=False)
    assert answer.status_code == 303
    sections = {item["heading"]: item["text"] for item in profile.split_sections(profile.read_profile())}
    assert sections["Kern"].strip() == "Nieuw feit.\n- Een punt"
    assert sections["Werkvoorkeuren"].strip() == "Buiten werken."
    assert any("aangepast: Kern" in (row["note"] or "") for row in store.list_profile_versions(5))
    assert client.post("/profile/sectie", data={"heading": "Bestaat niet", "text": "x"}).status_code == 404
