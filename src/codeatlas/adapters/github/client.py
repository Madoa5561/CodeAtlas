"""Bounded read-only GitHub REST client."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx

from codeatlas.config.settings import GitHubSettings
from codeatlas.domain.errors import (
    GitHubAuthenticationError,
    GitHubNotFoundError,
    GitHubProviderError,
)
from codeatlas.domain.github_models import (
    GitHubRepositoryInfo,
    GitHubRepositoryRef,
    IssueInfo,
    PullRequestDiff,
    PullRequestInfo,
)

_MAX_RESPONSE_BYTES = 4_000_000
_MAX_DIFF_CHARS = 200_000
_MAX_BODY_CHARS = 20_000
_MAX_ISSUES = 30
_MAX_BACKOFF_SECONDS = 10.0


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _text(value: object, limit: int = _MAX_BODY_CHARS) -> str:
    return value[:limit] if isinstance(value, str) else ""


class GitHubRestClient:
    """Minimal read-only GitHub API adapter with safe failures."""

    def __init__(
        self,
        settings: GitHubSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if transport is not None and client is not None:
            raise ValueError("provide either an HTTP client or a transport, not both")
        if settings.enabled and (
            settings.token is None or not settings.token.get_secret_value().strip()
        ):
            raise ValueError("github.token is required when GitHub is enabled")
        self._settings = settings
        self._base_url = str(settings.api_url).rstrip("/")
        self._token = settings.token.get_secret_value() if settings.token is not None else ""
        self._owns_client = client is None
        self._client = client if client is not None else self._create_client(transport)
        self._closed = False

    @classmethod
    def from_settings(
        cls,
        settings: GitHubSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> GitHubRestClient:
        """Build the client from validated settings."""
        return cls(settings, transport=transport, client=client)

    @property
    def enabled(self) -> bool:
        """Return whether GitHub integration is enabled."""
        return self._settings.enabled

    async def get_repository(self, repository: GitHubRepositoryRef) -> GitHubRepositoryInfo:
        """Fetch bounded repository metadata."""
        payload = await self._json("GET", f"/repos/{repository.full_name}")
        full_name = payload.get("full_name")
        html_url = payload.get("html_url")
        if not isinstance(full_name, str) or not isinstance(html_url, str):
            raise GitHubProviderError()
        description = payload.get("description")
        default_branch = payload.get("default_branch")
        return GitHubRepositoryInfo(
            full_name=full_name,
            description=description if isinstance(description, str) else None,
            default_branch=default_branch if isinstance(default_branch, str) else None,
            url=html_url,
            private=bool(payload.get("private")),
        )

    async def get_pull_request(
        self, repository: GitHubRepositoryRef, number: int
    ) -> PullRequestInfo:
        """Fetch one pull request with bounded body text."""
        payload = await self._json("GET", f"/repos/{repository.full_name}/pulls/{number}")
        return self._pull_request(payload, number)

    async def get_pull_request_diff(
        self, repository: GitHubRepositoryRef, number: int
    ) -> PullRequestDiff:
        """Fetch one pull request unified diff and its head revision."""
        pull = await self.get_pull_request(repository, number)
        response = await self._request(
            "GET",
            f"/repos/{repository.full_name}/pulls/{number}",
            headers={"Accept": "application/vnd.github.v3.diff"},
        )
        if response.status_code >= 400:
            raise self._status_error(response.status_code)
        diff = response.text[:_MAX_DIFF_CHARS]
        if not diff.strip():
            raise GitHubProviderError()
        return PullRequestDiff(
            number=number,
            head_sha=pull.head_sha,
            url=f"{pull.url}/files" if not pull.url.endswith("/files") else pull.url,
            diff=diff,
        )

    async def list_issues(
        self,
        repository: GitHubRepositoryRef,
        *,
        state: str,
        limit: int,
    ) -> tuple[IssueInfo, ...]:
        """List issues while excluding pull requests."""
        per_page = max(1, min(limit, _MAX_ISSUES))
        payload = await self._json(
            "GET",
            f"/repos/{repository.full_name}/issues",
            params={"state": state, "per_page": per_page, "sort": "updated", "direction": "desc"},
        )
        if not isinstance(payload, list):
            raise GitHubProviderError()
        issues: list[IssueInfo] = []
        for item in payload:
            if not isinstance(item, dict) or "pull_request" in item:
                continue
            number = item.get("number")
            title = item.get("title")
            html_url = item.get("html_url")
            if (
                not isinstance(number, int)
                or not isinstance(title, str)
                or not isinstance(html_url, str)
            ):
                continue
            user = item.get("user")
            labels = item.get("labels")
            issues.append(
                IssueInfo(
                    number=number,
                    title=title,
                    state=str(item.get("state", "unknown")),
                    url=html_url,
                    author=(
                        user.get("login")
                        if isinstance(user, dict) and isinstance(user.get("login"), str)
                        else None
                    ),
                    body=_text(item.get("body")),
                    labels=tuple(
                        label["name"]
                        for label in labels
                        if isinstance(label, dict) and isinstance(label.get("name"), str)
                    )
                    if isinstance(labels, list)
                    else (),
                    created_at=_datetime(item.get("created_at")),
                    updated_at=_datetime(item.get("updated_at")),
                )
            )
        return tuple(issues)

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
        )

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "CodeAtlas",
        }
        if extra:
            headers.update(extra)
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if self._closed or self._client.is_closed:
            raise GitHubProviderError()
        for attempt in range(2):
            try:
                response = await self._client.request(
                    method,
                    f"{self._base_url}{path}",
                    params=params,
                    headers=self._headers(headers),
                )
            except (httpx.ConnectError, httpx.TimeoutException):
                if attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                raise GitHubProviderError() from None
            except httpx.HTTPError:
                raise GitHubProviderError() from None
            if response.status_code in {429} or 500 <= response.status_code <= 599:
                if attempt == 0:
                    retry_after = response.headers.get("retry-after")
                    try:
                        delay = min(float(retry_after), _MAX_BACKOFF_SECONDS)
                    except (TypeError, ValueError):
                        delay = 0.5
                    await asyncio.sleep(max(0.0, delay))
                    continue
                raise GitHubProviderError() from None
            if len(response.content) > _MAX_RESPONSE_BYTES:
                raise GitHubProviderError()
            return response
        raise GitHubProviderError()

    async def _json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
    ) -> Any:
        response = await self._request(method, path, params=params)
        if response.status_code in {401, 403}:
            raise GitHubAuthenticationError()
        if response.status_code == 404:
            raise GitHubNotFoundError()
        if response.status_code >= 400:
            raise GitHubProviderError()
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            raise GitHubProviderError() from None

    @staticmethod
    def _status_error(status_code: int) -> Exception:
        if status_code in {401, 403}:
            return GitHubAuthenticationError()
        if status_code == 404:
            return GitHubNotFoundError()
        return GitHubProviderError()

    def _pull_request(self, payload: Any, number: int) -> PullRequestInfo:
        if not isinstance(payload, dict):
            raise GitHubProviderError()
        head = payload.get("head")
        base = payload.get("base")
        user = payload.get("user")
        if not isinstance(head, dict) or not isinstance(base, dict):
            raise GitHubProviderError()
        head_sha = head.get("sha")
        base_sha = base.get("sha")
        title = payload.get("title")
        html_url = payload.get("html_url")
        if (
            not isinstance(head_sha, str)
            or not isinstance(base_sha, str)
            or not isinstance(title, str)
            or not isinstance(html_url, str)
        ):
            raise GitHubProviderError()
        return PullRequestInfo(
            number=number,
            title=title,
            state=str(payload.get("state", "unknown")),
            url=html_url,
            head_sha=head_sha,
            base_sha=base_sha,
            author=(
                user.get("login")
                if isinstance(user, dict) and isinstance(user.get("login"), str)
                else None
            ),
            body=_text(payload.get("body")),
            created_at=_datetime(payload.get("created_at")),
            updated_at=_datetime(payload.get("updated_at")),
            merged=bool(payload.get("merged")),
            additions=int(payload.get("additions") or 0),
            deletions=int(payload.get("deletions") or 0),
            changed_files=int(payload.get("changed_files") or 0),
        )
