"""UI rendering contracts, without touching personal data or starting the LLM."""

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.web import _amsterdam

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"


def _register_filters(env):
    env.filters["amsterdam"] = _amsterdam
    env.globals["csrf_input"] = lambda request: ""
    return env


def render(name, path="/", cookies=None, role="admin", **context):
    env = _register_filters(
        Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape())
    )
    return BeautifulSoup(
        env.get_template(name).render(
            request=SimpleNamespace(
                url=SimpleNamespace(path=path),
                cookies=cookies or {},
                state=SimpleNamespace(
                    user={
                        "id": "legacy",
                        "username": "owner",
                        "display_name": "Owner",
                        "role": role,
                    },
                    csrf_token="test",
                ),
            ),
            **context,
        ),
        "html.parser",
    )


def test_all_templates_compile():
    from app.web import templates

    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape())
    env.filters.update(templates.env.filters)  # the app's own filters, such as prose
    for name in env.list_templates():
        env.get_template(name)


def test_navigation_marks_only_current_section_and_has_skip_link():
    for path, expected in [
        ("/", "/"),
        ("/jobs/120", "/jobs"),
        ("/profile", "/profile"),
        ("/account", "/account"),
    ]:
        html = render("base.html", path, role="user")
        active = html.select('.top [aria-current="page"]')
        assert len(active) == 1
        assert active[0]["href"] == expected
        assert html.select_one('.skip-link[ href="#main-content"]')
        assert html.select_one("main#main-content")
        assert "deepseek" not in html.footer.get_text()


def test_admin_navigation_is_settings_only():
    html = render("base.html", "/admin")
    labels = [link.get_text(strip=True) for link in html.select(".rail-nav a")]
    assert labels == ["Bronnen", "Account", "Gebruikers", "Systeem"]
    active = html.select('.top [aria-current="page"]')
    assert len(active) == 1
    assert active[0]["href"] == "/admin"
    assert not html.select(".tabbar")
    assert html.select_one(".brand")["href"] == "/admin"


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


def test_today_leads_with_the_first_task_and_shows_the_week():
    task = {"title": "Bereid je gesprek bij Gemeente voor", "why": "Ken de vragen.", "href": "/jobs/1#inzicht", "label": "Bereid je voor", "tag": "Gesprek"}
    other = {"title": "Neem contact op met DEPT", "why": "Je reageerde 9 dagen geleden.", "href": "/sollicitaties", "label": "Zo pak je het aan", "tag": "Nabellen"}
    html = render(
        "dashboard.html",
        has_profile=True,
        tasks=[task, other],
        summary="Vandaag: 2 dingen om te doen.",
        matches=[],
        fresh=0,
        week={"sent_week": 2, "waiting": 3, "talks": 1, "rate": None},
        tip="Zet je resultaten bovenaan.",
        has_jobs=True,
    )
    assert html.select_one(".next-step h2").get_text() == task["title"]
    assert [el.get_text(strip=True) for el in html.select(".task h3")] == [other["title"]]
    assert [el.get_text() for el in html.select(".week-numbers dd")] == ["2", "3", "1"]
    assert "Zet je resultaten bovenaan." in html.get_text()
    assert len(html.select("h1")) == 1 and "uitgebreid beoordeeld" not in html.get_text()


def test_long_running_banners_poll_instead_of_meta_refresh():
    for name in ("profile.html", "sources.html", "_ui.html"):
        text = (TEMPLATES / name).read_text()
        assert 'http-equiv="refresh"' not in text
        assert "data-auto-refresh=" in text
    assert "ui.wait(" in (TEMPLATES / "job_detail.html").read_text()


def test_theme_cookie_is_applied_to_base_and_login():
    assert render("base.html", cookies={"theme": "dark"}).html["data-theme"] == "dark"
    assert render("login.html", "/login", cookies={"theme": "light"}).html["data-theme"] == "light"
    assert not render("base.html", cookies={}).html.has_attr("data-theme")


def test_amsterdam_filter_converts_utc_to_local_time():
    assert _amsterdam("2026-10-25T23:30:00+00:00").startswith("2026-10-26T00:30")
    assert _amsterdam("2026-07-01T12:00:00Z").startswith("2026-07-01T14:00")
    assert _amsterdam(None) is None


def test_status_tabs_show_counts_when_provided():
    html = render(
        "jobs.html",
        "/jobs",
        jobs=[],
        sources=[],
        counts={"total": 0, "matched": 0},
        status_links=[
            {"label": "Alle", "value": "", "count": 4, "url": "/jobs"},
            {"label": "Bewaard", "value": "saved", "count": 2, "url": "/jobs?status=saved"},
        ],
        status_filter="",
        query="",
        source_filter="",
        within="1",
        stale="0",
        stale_active=False,
        stale_url="/jobs",
        filter_chips=[],
        reset_url="/jobs",
        sort="rank",
        excluded=0,
        page=1,
        pages=1,
        per_page=50,
        total_filtered=0,
        pager_base="/jobs",
        scan_status="idle",
        match_status="idle",
        match_summary=None,
        feedback={"counts": {"up": 0, "down": 0}},
        geo_home={
            "label": "Amsterdam",
            "configured": True,
            "bike_minutes": 30,
            "transit_minutes": 35,
        },
        queue=0,
        return_to="/jobs",
    )
    tabs = {
        tab["data-status"]: tab.get_text(" ", strip=True) for tab in html.select(".status-tabs a")
    }
    assert tabs[""] == "Alle 4"
    assert tabs["saved"] == "Bewaard 2"


def test_admin_sources_hide_personal_preference_toggle():
    source = {
        "id": "bron-a",
        "name": "Bron A",
        "enabled": True,
        "last_status": "ok",
        "last_scan_at": "2026-10-25T10:00:00+00:00",
        "notes": "",
        "kind": "rss",
    }
    context = {
        "sources": [source],
        "user_sources": {"bron-a": {"selected": True, "reason": ""}},
        "jobs_by_source": {"bron-a": 1},
        "scan_status": "idle",
        "summary": None,
        "counts": {"total": 1},
    }
    admin_html = render("sources.html", "/sources", **context)
    assert "Voor mij" not in admin_html.get_text()
    assert "Voor iedereen uitzetten" in admin_html.get_text()

    user_html = render("sources.html", "/sources", role="user", **context)
    assert "Voor mij aan" in user_html.get_text()
    assert "Voor iedereen uitzetten" not in user_html.get_text()
