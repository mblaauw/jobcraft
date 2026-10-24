"""Adapter data and transport interfaces; optional values stay explicit."""

from typing import Any, Protocol, TypedDict

import httpx


class JobItem(TypedDict):
    source_id: str
    url: str
    title: str
    company: str
    location: str
    hours: str
    description: str
    posted_at: str | None
    lat: float | None
    lon: float | None
    content_hash: str


class HTTPClient(Protocol):
    def get(self, url: str, **kwargs: Any) -> httpx.Response: ...


class Adapter(Protocol):
    def __call__(
        self,
        client: HTTPClient,
        source: dict,
        entry: dict,
        summary: dict,
        limit_per_source: int | None = None,
    ) -> None: ...
