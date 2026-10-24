import gzip

import httpx
import pytest

from app.source_http import USER_AGENT, PoliteClient


class Clock:
    def __init__(self):
        self.value = 0.0
        self.waits = []

    def now(self):
        return self.value

    def sleep(self, delay):
        self.waits.append(delay)
        self.value += delay


def test_compressed_304_reuses_decoded_cached_body(tmp_path):
    count = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        count.append(1)
        return (
            httpx.Response(
                200,
                content=gzip.compress(b"public body"),
                headers={"ETag": "gzip", "Content-Encoding": "gzip"},
            )
            if len(count) == 1
            else httpx.Response(304, headers={"Content-Encoding": "gzip"})
        )

    client, _ = transport(handler, tmp_path)
    assert client.get("https://example.test/job").text == "public body"
    assert client.get("https://example.test/job").text == "public body"


def test_cache_eviction_and_malformed_mapping_are_safe(tmp_path):
    client, _ = transport(
        lambda request: httpx.Response(
            404 if request.url.path == "/robots.txt" else 200, text="public"
        ),
        tmp_path,
    )
    for index in range(65):
        client.get(f"https://example.test/job/{index}")
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 64
    files[0].write_text("[]")
    assert client._load(files[0].stem) is None


def test_request_error_retries_without_real_sleep(tmp_path):
    calls = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("synthetic", request=request)
        return httpx.Response(200)

    client, _ = transport(handler, tmp_path)
    assert client.get("https://example.test/job").status_code == 200
    assert len(calls) == 2


def transport(handler, tmp_path, **kwargs):
    clock = Clock()
    raw = httpx.Client(transport=httpx.MockTransport(handler), **kwargs)
    return PoliteClient(client=raw, cache_dir=tmp_path, sleep=clock.sleep, clock=clock.now), clock


def test_robots_denial_prevents_detail_and_redirect_destination(tmp_path):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        return httpx.Response(302, headers={"Location": "https://other.test/private"})

    client, _ = transport(handler, tmp_path)
    with pytest.raises(httpx.RequestError, match="disallows"):
        client.get("https://example.test/start")
    assert "https://other.test/robots.txt" in requests
    assert "https://other.test/private" not in requests


def test_unavailable_robots_fail_closed_and_404_allows(tmp_path):
    client, _ = transport(lambda request: httpx.Response(503), tmp_path)
    with pytest.raises(httpx.HTTPStatusError, match="Robots"):
        client.get("https://example.test/job")
    client, _ = transport(
        lambda request: httpx.Response(404 if request.url.path == "/robots.txt" else 200), tmp_path
    )
    assert client.get("https://example.test/job").status_code == 200


@pytest.mark.parametrize("status", [429, 501, 507])
def test_retries_pacing_and_retry_after_cap(tmp_path, status):
    calls = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 2")
        calls.append(str(request.url))
        return httpx.Response(status if len(calls) == 1 else 200, headers={"Retry-After": "999"})

    client, clock = transport(handler, tmp_path)
    assert client.get("https://example.test/job").status_code == 200
    assert len(calls) == 2 and 30 in clock.waits
    assert clock.value >= 32


def test_conditional_cache_reuses_validated_body_and_varies_effective_headers(tmp_path):
    seen = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(dict(request.headers))
        if "if-none-match" in request.headers:
            return httpx.Response(304)
        return httpx.Response(
            200,
            content=request.headers.get("accept-language", "nl").encode(),
            headers={"ETag": "v1"},
        )

    first, _ = transport(handler, tmp_path, headers={"Accept-Language": "nl"})
    assert first.get("https://example.test/job").text == "nl"
    assert first.get("https://example.test/job").text == "nl"
    assert seen[-1]["if-none-match"] == "v1"
    other, _ = transport(handler, tmp_path, headers={"Accept-Language": "en"})
    assert other.get("https://example.test/job").text == "en"
    assert "if-none-match" not in seen[-1]
    assert (
        other.get("https://example.test/job", headers={"If-None-Match": "caller-owned"}).status_code
        == 304
    )


def test_public_transport_rejects_auth_and_never_sends_response_cookies(tmp_path):
    with pytest.raises(ValueError, match="public"):
        PoliteClient(client=httpx.Client(auth=("person", "secret")), cache_dir=tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, text="User-agent: *\nAllow: /", headers={"Set-Cookie": "session=private"}
        )

    client, _ = transport(handler, tmp_path)
    client.get("https://example.test/job", headers={"User-Agent": "OtherBot"})
    assert all("cookie" not in request.headers for request in requests)
    assert all(request.headers["user-agent"] == USER_AGENT for request in requests)
    assert not list(tmp_path.glob("*.json"))
    with pytest.raises(ValueError, match="public"):
        client.get("https://example.test/job", headers={"Authorization": "secret"})


def test_private_and_oversized_responses_not_persisted(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"x" * (2 * 1024 * 1024), headers={"ETag": "big"})

    client, _ = transport(handler, tmp_path)
    client.get("https://example.test/job")
    assert not list(tmp_path.glob("*.json"))
    client.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, text="private", headers={"Cache-Control": "private"}
            )
        )
    )
    client.get("https://example.test/job")
    assert not list(tmp_path.glob("*.json"))
