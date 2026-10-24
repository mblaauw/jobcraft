"""rss source adapter."""
from __future__ import annotations

from xml.etree import ElementTree

from .. import store
from .common import _tally, make_job, strip_html


def _scan_rss(client, source, entry, summary, limit_per_source=None) -> None:
    feed_url = source.get("feed_url") or (
        source["base_url"].rstrip("/") + source.get("path", "/feed/")
    )
    response = client.get(feed_url)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    items = [element for element in root.iter() if element.tag.split("}")[-1] in ("item", "entry")]
    limit = limit_per_source or int(source.get("per_page", 30))
    for item in items[:limit]:

        def child_text(names) -> str:
            for child in item:
                if child.tag.split("}")[-1] in names:
                    return (child.text or "").strip()
            return ""

        title = child_text(("title",))
        link_element = next((child for child in item if child.tag.split("}")[-1] == "link"), None)
        link = ""
        if link_element is not None:
            link = (link_element.text or "").strip() or link_element.get("href", "")
        description = strip_html(child_text(("description", "summary", "content")))
        posted_at = child_text(("pubDate", "published", "updated"))
        if not title or not link:
            continue
        _, created = store.upsert_job(
            make_job(
                source["id"],
                link,
                title,
                description=description,
                posted_at=posted_at,
            )
        )
        _tally(entry, summary, created)
    store.update_source_status(source["id"], "ok")



scan = _scan_rss


KINDS = ('rss',)
