"""SSRF controls, content limits, and HTTP tool tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from codeatlas.adapters.http.fetch import SafeHttpFetcher
from codeatlas.application.tools.resolver import ToolServices
from codeatlas.application.tools.schemas import FetchHttpInput
from codeatlas.application.tools.web_tools import fetch_http
from codeatlas.config.settings import HttpToolSettings
from codeatlas.domain.agent_models import ToolContext
from codeatlas.domain.errors import ForbiddenError, HttpFetchError
from codeatlas.domain.indexing_models import IndexGenerationStatus
from codeatlas.domain.repository_models import (
    RepositoryDescriptor,
    RepositoryId,
    RepositoryLocation,
)
from codeatlas.domain.retrieval_models import IndexSnapshotSummary

_REPO = RepositoryId("repo_" + "a" * 32)
_SHA = "b" * 40
_NOW = datetime(2026, 10, 25, tzinfo=UTC)
_PUBLIC_IP = "93.184.216.34"


def settings(**overrides: object) -> HttpToolSettings:
    values: dict[str, object] = {
        "enabled": True,
        "allowed_domains": ("example.com",),
        "respect_robots_txt": True,
        "max_response_bytes": 20_000,
    }
    values.update(overrides)
    return HttpToolSettings(**values)


def resolver_for(address: str = _PUBLIC_IP):
    async def resolve(host: str, port: int) -> tuple[str, ...]:
        return (address,)

    return resolve


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
    if path == "/page":
        return httpx.Response(
            200,
            text="<html><head><title>Docs</title></head><body><h1>Hello</h1><script>bad()</script><p>World</p></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    if path == "/redirect":
        return httpx.Response(302, headers={"location": "http://127.0.0.1/"})
    if path == "/redirect-ok":
        return httpx.Response(302, headers={"location": "http://example.com/page"})
    if path == "/binary":
        return httpx.Response(
            200, content=b"\x00\x01", headers={"content-type": "application/octet-stream"}
        )
    if path == "/big":
        return httpx.Response(200, text="x" * 25_000, headers={"content-type": "text/plain"})
    return httpx.Response(404)


def fetcher(**overrides: object) -> SafeHttpFetcher:
    return SafeHttpFetcher(
        settings(**overrides),
        transport=httpx.MockTransport(handler),
        resolver=resolver_for(),
    )


@pytest.mark.asyncio
async def test_html_is_extracted_with_title_and_scripts_removed() -> None:
    client = fetcher()
    try:
        doc = await client.fetch("http://example.com/page")
    finally:
        await client.aclose()
    assert doc.title == "Docs"
    assert "Hello" in doc.text and "World" in doc.text
    assert "bad()" not in doc.text


@pytest.mark.asyncio
async def test_redirects_are_followed_and_revalidated() -> None:
    client = fetcher()
    try:
        doc = await client.fetch("http://example.com/redirect-ok")
        with pytest.raises(ForbiddenError):
            await client.fetch("http://example.com/redirect")
    finally:
        await client.aclose()
    assert doc.url == "http://example.com/page"


@pytest.mark.asyncio
async def test_robots_txt_is_enforced() -> None:
    client = fetcher()
    try:
        with pytest.raises(ForbiddenError):
            await client.fetch("http://example.com/private/page")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_content_type_and_size_limits() -> None:
    client = fetcher()
    try:
        with pytest.raises(HttpFetchError):
            await client.fetch("http://example.com/binary")
        doc = await client.fetch("http://example.com/big")
    finally:
        await client.aclose()
    assert doc.truncated is True
    assert len(doc.text) <= 100_000


@pytest.mark.asyncio
async def test_head_returns_metadata_without_body() -> None:
    client = fetcher()
    try:
        doc = await client.fetch("http://example.com/page", method="HEAD")
    finally:
        await client.aclose()
    assert doc.status == 200
    assert doc.text == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://example.com:22/",
        "ftp://example.com/file",
        "http://user:pass@example.com/",
        "http://evil.example.net/",
    ],
)
async def test_ssrf_and_scheme_controls_reject_unsafe_urls(url: str) -> None:
    client = fetcher()
    try:
        with pytest.raises(ForbiddenError):
            await client.fetch(url)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_dns_resolving_to_private_address_is_rejected() -> None:
    client = SafeHttpFetcher(
        settings(),
        transport=httpx.MockTransport(handler),
        resolver=resolver_for("10.0.0.5"),
    )
    try:
        with pytest.raises(ForbiddenError):
            await client.fetch("http://example.com/page")
    finally:
        await client.aclose()


class FakeProvider:
    async def get_repository(self, repository_id: RepositoryId) -> RepositoryDescriptor:
        return _descriptor()


class FakeRegistry:
    def list_all(self) -> tuple[RepositoryDescriptor, ...]:
        return (_descriptor(),)

    def get(self, repository_id: RepositoryId) -> RepositoryDescriptor | None:
        return _descriptor()


class FakeIndex:
    async def get_active_snapshot(self, repository_id: RepositoryId) -> IndexSnapshotSummary:
        return IndexSnapshotSummary(
            repository_id=repository_id,
            commit_sha=_SHA,
            generation_id="gen_" + "c" * 32,
            generation_number=1,
            status=IndexGenerationStatus.ACTIVE,
        )


class FakeFetcher:
    async def fetch(self, url: str, *, method: str = "GET") -> object:
        assert method == "GET"
        from codeatlas.domain.web_models import WebDocument

        return WebDocument(
            url=url,
            status=200,
            content_type="text/html",
            title="Docs",
            text="Hello world",
            truncated=False,
        )

    async def aclose(self) -> None:
        return None


def _descriptor() -> RepositoryDescriptor:
    return RepositoryDescriptor(
        repository_id=_REPO,
        canonical_location=RepositoryLocation(Path(".")),
        name="linejs",
        default_branch="main",
        registered_at=_NOW,
    )


@pytest.mark.asyncio
async def test_fetch_tool_returns_citation_with_external_url() -> None:
    services = ToolServices(
        repository=FakeProvider(),  # type: ignore[arg-type]
        registry=FakeRegistry(),  # type: ignore[arg-type]
        index=FakeIndex(),  # type: ignore[arg-type]
        http=FakeFetcher(),
    )
    result = await fetch_http(
        services,
        ToolContext(request_id="req_http_1"),
        FetchHttpInput(url="http://example.com/page"),
    )
    assert result.evidence[0].retrieval_method == "http_fetch"
    assert result.evidence[0].external_url == "http://example.com/page"
    assert result.evidence[0].path.as_posix().startswith("web/example.com/")
    payload = json.loads(json.dumps(result.payload))
    assert payload["title"] == "Docs"
