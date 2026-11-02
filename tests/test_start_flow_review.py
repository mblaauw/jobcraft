"""Common CV formats and a base CV for people without a suitable Word file."""

import io

import pytest
from docx import Document

from app import config, cv_text, cv_uploads, docs, store, tenancy
from tests.test_jobs_page import client, soup  # noqa: F401


@pytest.mark.parametrize(
    "line, role",
    [
        ("Beleidsadviseur bij Gemeente Utrecht jan 2018 - mrt 2021", "Beleidsadviseur"),
        ("Projectleider, Acme 03-2015 - 06-2017", "Projectleider"),
        ("Teamleider 2012 - heden", "Teamleider"),
        ("Kok 04/2010 - 05/2011", "Kok"),
        ("januari 2019 tot heden Decaan", "Decaan"),
        ("Marketing manager 2016 - 2018", "Marketing manager"),
    ],
)
def test_roles_survive_common_date_formats(line, role):
    assert cv_text.read_cv(f"WERKERVARING\n{line}\nOPLEIDING\nHBO")["roles"] == [role]


def test_dates_are_not_mistaken_for_phone_numbers():
    found = cv_text.read_cv("WERKERVARING\nPlanner 03-2015 - 06-2017\nBel 06-12345678\n")
    text = found["sections"]["experience"]
    assert "03-2015 - 06-2017" in text and "12345678" not in text


def test_seniority_uses_the_year_after_a_month():
    assert cv_text.read_cv("WERKERVARING\nPlanner jan 2005 - mrt 2021\n")["seniority"] == "senior"


def test_name_after_a_cv_title_is_still_removed():
    found = cv_text.read_cv(
        "Curriculum Vitae\nJan de Vries\njan@x.nl\n\nProfiel\nJan de Vries werkt graag samen."
    )
    assert "Vries" not in found["sections"]["summary"]


def test_a_surname_is_not_suggested_as_home():
    found = cv_text.read_cv("Jan de Vries\nUtrecht\n\nProfiel\nWerkt graag samen met anderen.")
    assert found["place"] == "Utrecht"
    assert cv_text.read_cv("Jan de Vries\n\nProfiel\nWerkt graag samen.")["place"] == ""


@pytest.fixture()
def account(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")
    store.init_db()
    with tenancy.scope({"id": "owner-a", "username": "a", "display_name": "Sam Test", "role": "user"}):
        yield


SECTIONS = {"Werkervaring": "Planner bij Acme\n2015 - 2020", "Opleiding": "HBO"}


def test_plain_base_cv_can_be_rendered_for_a_vacancy(account, tmp_path):
    assert cv_uploads.create_generated(SECTIONS) is True
    assert cv_uploads.is_generated()
    assert docs.cv_identity()["name"] == ["Sam Test"]
    out = tmp_path / "out.docx"
    tailoring = {"profile": "Mijn profiel.", "strong_points": [f"Punt {i}" for i in range(1, 6)]}
    docs.render_cv({"title": "T"}, tailoring, out)
    text = "\n".join(paragraph.text for paragraph in Document(out).paragraphs)
    assert "Mijn profiel." in text and "Punt 5" in text and "Planner bij Acme" in text
    assert "{{" not in text


def test_plain_base_cv_never_overwrites_an_existing_one(account):
    source = config.seed_dir() / "cv.docx"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"own cv")
    assert cv_uploads.create_generated(SECTIONS) is False
    assert source.read_bytes() == b"own cv"
    assert not cv_uploads.is_generated()


def test_a_table_stays_under_its_own_heading():
    document = Document()
    document.add_paragraph("Werkervaring")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "mei 2019 - heden"
    table.cell(0, 1).text = "Beleidsadviseur"
    document.add_paragraph("Interesses")
    document.add_paragraph("Hardlopen")
    buffer = io.BytesIO()
    document.save(buffer)
    found = cv_text.read_cv(cv_text.extract_text(buffer.getvalue(), "cv.docx"))
    assert "Beleidsadviseur" in found["sections"]["experience"]
    assert "Hardlopen" not in found["sections"]["experience"]


def test_a_large_cv_gets_past_the_security_check(client):  # noqa: F811
    token = soup(client.get("/start/cv")).select_one('input[name="_csrf"]')["value"]
    for size, message in ((442_190, "niet lezen"), (6_000_000, "maximaal 5 MB")):
        client.headers.pop("x-csrf-token", None)
        response = client.post(
            "/start/cv",
            data={"_csrf": token},
            files={"cv": ("cv.pdf", b"x" * size, "application/pdf")},
            headers={"x-csrf-token": ""},
        )
        assert response.status_code == 200, size
        assert message in response.text


def test_an_expired_form_explains_itself_to_a_person(client):  # noqa: F811
    response = client.post(
        "/jobs/1/vote",
        data={"_csrf": "fout", "vote": "up"},
        headers={"accept": "text/html", "x-csrf-token": ""},
    )
    assert response.status_code == 403
    assert "Ververs de pagina" in response.text
    assert "CSRF" not in response.text
