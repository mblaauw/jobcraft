"""UI rendering contracts, without touching personal data or starting the LLM."""

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"


def render(name, path="/", **context):
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape())
    return BeautifulSoup(
        env.get_template(name).render(
            request=SimpleNamespace(
                url=SimpleNamespace(path=path),
                state=SimpleNamespace(
                    user={
                        "id": "legacy",
                        "username": "owner",
                        "display_name": "Owner",
                        "role": "admin",
                    },
                    csrf_token="test",
                ),
            ),
            **context,
        ),
        "html.parser",
    )


def test_all_templates_compile():
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape())
    for name in env.list_templates():
        env.get_template(name)


def test_navigation_marks_only_current_section_and_has_skip_link():
    for path, expected in [
        ("/", "/"),
        ("/jobs/120", "/jobs"),
        ("/profile", "/profile"),
        ("/admin", "/admin"),
    ]:
        html = render("base.html", path)
        active = html.select('.top [aria-current="page"]')
        assert len(active) == 1
        assert active[0]["href"] == expected
        assert html.select_one('.skip-link[ href="#main-content"]')
        assert html.select_one("main#main-content")
        assert "deepseek" not in html.footer.get_text()


def test_interview_empty_coverage_and_accessible_composer():
    html = render(
        "interview.html",
        "/interview",
        sessions=[],
        session_id="example",
        messages=[{"role": "assistant", "text": "Wat zoek je?"}],
        coverage={"covered": 0, "total": 0, "question_count": 0, "topics": []},
    )
    assert html.select_one("progress")["max"] == "1"
    assert html.select_one('label[for="chat-input"]')
    assert html.select_one('#chat-log[role="log"]')
    assert html.select_one('#chat-status[aria-live="polite"]')
    assert html.select_one('script[src="/static/chat.js"]')


def test_job_filters_preserve_search_source_and_travel_and_show_empty_state():
    html = render(
        "jobs.html",
        "/jobs",
        jobs=[],
        sources=[],
        counts={"total": 12, "matched": 3},
        query="groen & buiten",
        source_filter="test",
        status_filter="saved",
        within="0",
        queue=0,
        geo_home={"label": "Amsterdam", "bike_minutes": 30, "transit_minutes": 35},
        match_status="idle",
        feedback={"counts": {"up": 0, "down": 0}},
        total_filtered=0,
        pages=1,
        per_page=50,
        sort="rank",
        status_links=[
            {
                "label": label,
                "value": value,
                "url": "/jobs?"
                + urlencode(
                    {"status": value, "q": "groen & buiten", "source": "test", "within": "0"}
                ),
            }
            for value, label in [
                ("", "Alle vacatures"),
                ("new", "Nieuw"),
                ("saved", "Bewaard"),
                ("applied", "Gereageerd"),
                ("ignored", "Genegeerd"),
            ]
        ],
    )
    tabs = html.select(".status-tabs a")
    assert len(tabs) == 5
    assert html.select_one('.status-tabs [aria-current="page"]').get_text() == "Bewaard"
    for tab in tabs:
        query = parse_qs(urlsplit(tab["href"]).query)
        assert query["q"] == ["groen & buiten"]
        assert query["source"] == ["test"]
        assert query["within"] == ["0"]
    assert html.select_one('.jobs-filters input[type="checkbox"][name="within"]') is not None
    assert not html.select_one('.jobs-filters input[type="checkbox"][name="within"]').has_attr(
        "checked"
    )
    assert "Geen vacatures in dit overzicht" in html.get_text()
    assert html.select_one(".vacancy-list .empty-state")
    assert not html.select("table")


def test_dashboard_diagnostics_are_collapsed_and_metrics_are_real_values():
    html = render(
        "dashboard.html",
        jobs={"total": 12, "matched": 7},
        queue=2,
        documents=3,
        profile_state={"exists": True, "updated": "2026-10-18"},
        sessions=[],
        seed={"ready": True, "dir": "example"},
        usage={"calls": 1, "tin": 2, "tout": 3, "cost": 0.01},
        next_step={"label": "Bekijk vacatures", "href": "/jobs", "detail": "Een volgende stap"},
    )
    assert [el.get_text() for el in html.select(".metric strong")] == ["12", "7", "2", "3"]
    assert not html.select_one(".technical-details").has_attr("open")
    assert len(html.select("h1")) == 1
