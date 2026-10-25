"""Ports for the safe outbound HTTP tool."""

from __future__ import annotations

from typing import Literal, Protocol

from codeatlas.domain.web_models import WebDocument


class HttpFetchPort(Protocol):
    """Fetch bounded public HTTP text with SSRF controls enforced by the adapter."""

    async def fetch(self, url: str, *, method: Literal["GET", "HEAD"] = "GET") -> WebDocument: ...

    async def aclose(self) -> None: ...
