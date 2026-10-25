"""SSRF-safe bounded HTTP fetcher for the M7 web tool."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from codeatlas.config.settings import HttpToolSettings
from codeatlas.domain.errors import ForbiddenError, HttpFetchError
from codeatlas.domain.web_models import WebDocument

Resolver = Callable[[str, int], Awaitable[tuple[str, ...]]]

_ALLOWED_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "application/json",
}
_ALLOWED_PORTS = {80, 443}
_MAX_ROBOTS_BYTES = 100_000
_MAX_TEXT_CHARS = 100_000
_USER_AGENT = "CodeAtlas/0.6 (+https://github.com/Madoa5561/CodeAtlas)"
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


async def _default_resolver(host: str, port: int) -> tuple[str, ...]:
    """Resolve a hostname only for SSRF validation."""
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError:
        return ()
    return tuple({str(info[4][0]) for info in infos})


def _is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


@dataclass(frozen=True, slots=True)
class _RawResponse:
    status: int
    headers: httpx.Headers
    body: bytes
    truncated: bool


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in {"script", "style", "noscript", "template"}:
            self._skip += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template"} and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title_parts.append(data)
        self.text_parts.append(data)


def _extract_html(text: str) -> tuple[str | None, str]:
    parser = _TextExtractor()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return None, " ".join(text.split())
    title = " ".join(" ".join(parser.title_parts).split()) or None
    body = " ".join(" ".join(parser.text_parts).split())
    return title, body[:_MAX_TEXT_CHARS]


class SafeHttpFetcher:
    """GET/HEAD fetcher with DNS/IP/redirect/domain/content controls."""

    def __init__(
        self,
        settings: HttpToolSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        if transport is not None and client is not None:
            raise ValueError("provide either an HTTP client or a transport, not both")
        self._settings = settings
        self._resolver = resolver or _default_resolver
        self._owns_client = client is None
        self._client = client if client is not None else self._create_client(transport)
        self._robots: dict[str, RobotFileParser | None] = {}
        self._closed = False

    async def fetch(self, url: str, *, method: Literal["GET", "HEAD"] = "GET") -> WebDocument:
        """Fetch one bounded document, validating every redirect target."""
        if method not in {"GET", "HEAD"}:
            raise ForbiddenError("Only GET and HEAD are permitted.")
        current = url
        current_method = method
        for _ in range(self._settings.max_redirects + 1):
            await self._validate_url(current)
            if current_method == "GET" and self._settings.respect_robots_txt:
                if not await self._allowed_by_robots(current):
                    raise ForbiddenError("The URL is disallowed by robots.txt.")
            raw = await self._request_once(current, current_method)
            if raw.status in _REDIRECT_STATUSES:
                location = raw.headers.get("location")
                if not location:
                    raise HttpFetchError()
                current = urljoin(current, location)
                if raw.status == 303:
                    current_method = "GET"
                continue
            if raw.status >= 400 or raw.status < 200:
                raise HttpFetchError()
            content_type = raw.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type not in _ALLOWED_CONTENT_TYPES:
                raise HttpFetchError()
            if current_method == "HEAD":
                return WebDocument(
                    url=current,
                    status=raw.status,
                    content_type=content_type,
                    title=None,
                    text="",
                    truncated=raw.truncated,
                )
            text = raw.body.decode("utf-8", errors="replace")
            if content_type in {"text/html", "application/xhtml+xml"}:
                title, extracted = _extract_html(text)
                if not extracted:
                    extracted = " ".join(text.split())[:_MAX_TEXT_CHARS]
            else:
                title = None
                extracted = text[:_MAX_TEXT_CHARS]
            return WebDocument(
                url=current,
                status=raw.status,
                content_type=content_type,
                title=title,
                text=extracted,
                truncated=raw.truncated,
            )
        raise HttpFetchError()

    async def aclose(self) -> None:
        """Release owned resources."""
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    def _create_client(self, transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(self._settings.timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            headers={"User-Agent": _USER_AGENT},
        )

    async def _validate_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            raise ForbiddenError("Only HTTP and HTTPS URLs are permitted.")
        if parts.username is not None or parts.password is not None:
            raise ForbiddenError("URLs with credentials are not permitted.")
        if not parts.hostname:
            raise ForbiddenError("The URL must have a hostname.")
        try:
            host = parts.hostname.encode("idna").decode("ascii").casefold().rstrip(".")
        except UnicodeError:
            raise ForbiddenError("The URL hostname is not permitted.") from None
        if (
            not host
            or host == "localhost"
            or host.endswith((".localhost", ".local", ".internal", ".home.arpa"))
        ):
            raise ForbiddenError("The URL host is not permitted.")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ForbiddenError("IP literal URLs are not permitted.")
        if not self._domain_allowed(host):
            raise ForbiddenError("The URL domain is not allowed.")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port not in _ALLOWED_PORTS:
            raise ForbiddenError("The URL port is not permitted.")
        addresses = await self._resolver(host, port)
        if not addresses:
            raise HttpFetchError()
        if any(not _is_public_ip(address) for address in addresses):
            raise ForbiddenError("The URL resolves to a non-public address.")

    def _domain_allowed(self, host: str) -> bool:
        for allowed in self._settings.allowed_domains:
            normalized = allowed.strip().casefold().rstrip(".")
            if host == normalized or host.endswith(f".{normalized}"):
                return True
        return False

    async def _allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if host not in self._robots:
            self._robots[host] = await self._load_robots(parts.scheme, host)
        parser = self._robots[host]
        return parser is None or parser.can_fetch(_USER_AGENT, url)

    async def _load_robots(self, scheme: str, host: str) -> RobotFileParser | None:
        robots_url = f"{scheme}://{host}/robots.txt"
        try:
            await self._validate_url(robots_url)
            raw = await self._request_once(
                robots_url,
                "GET",
                max_bytes=_MAX_ROBOTS_BYTES,
            )
            if raw.status != 200:
                return None
            parser = RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(raw.body.decode("utf-8", errors="replace").splitlines())
            return parser
        except Exception:
            return None

    async def _request_once(
        self,
        url: str,
        method: str,
        *,
        max_bytes: int | None = None,
    ) -> _RawResponse:
        if self._closed or self._client.is_closed:
            raise HttpFetchError()
        limit = max_bytes if max_bytes is not None else self._settings.max_response_bytes
        try:
            async with self._client.stream(method, url) as response:
                collected = bytearray()
                truncated = False
                if method != "HEAD":
                    async for chunk in response.aiter_bytes():
                        remaining = limit + 1 - len(collected)
                        collected.extend(chunk[:remaining])
                        if len(collected) > limit:
                            del collected[limit:]
                            truncated = True
                            break
                return _RawResponse(
                    status=response.status_code,
                    headers=response.headers,
                    body=bytes(collected),
                    truncated=truncated,
                )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
            raise HttpFetchError() from None
