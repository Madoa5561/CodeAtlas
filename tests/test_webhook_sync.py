"""Coalesced webhook-driven incremental indexing tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from codeatlas.application.webhook_sync import WebhookSyncService
from codeatlas.domain.indexing_models import IndexingRequest
from codeatlas.domain.repository_models import RepositoryId

_REPO = RepositoryId("repo_" + "a" * 32)


class FakeIndexing:
    """Indexing service double with an optional gate."""

    def __init__(self, *, gate: asyncio.Event | None = None) -> None:
        self.calls: list[IndexingRequest] = []
        self.started = asyncio.Event()
        self.gate = gate
        self.fail = False

    async def create_index(self, request: IndexingRequest) -> object:
        self.calls.append(request)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("index failed")
        return SimpleNamespace(
            snapshot=SimpleNamespace(
                generation_id=SimpleNamespace(value="gen_" + "b" * 32),
            ),
            status=SimpleNamespace(value="active"),
        )


@pytest.mark.asyncio
async def test_only_matching_push_events_are_scheduled() -> None:
    indexing = FakeIndexing()
    service = WebhookSyncService(indexing, _REPO, "evex-dev/linejs")  # type: ignore[arg-type]

    assert (
        await service.request(event="issues", repository="evex-dev/linejs", delivery_id="d1")
        == "ignored"
    )
    assert (
        await service.request(event="push", repository="other/repo", delivery_id="d2") == "ignored"
    )
    await service.wait_idle()
    assert indexing.calls == []

    assert (
        await service.request(event="push", repository="evex-dev/linejs", delivery_id="d3")
        == "scheduled"
    )
    await service.wait_idle()
    assert len(indexing.calls) == 1
    assert indexing.calls[0].incremental is True
    assert indexing.calls[0].revision == "HEAD"


@pytest.mark.asyncio
async def test_concurrent_push_events_coalesce_into_one_follow_up() -> None:
    gate = asyncio.Event()
    indexing = FakeIndexing(gate=gate)
    service = WebhookSyncService(indexing, _REPO, "evex-dev/linejs")  # type: ignore[arg-type]

    assert (
        await service.request(event="push", repository="evex-dev/linejs", delivery_id="d1")
        == "scheduled"
    )
    await indexing.started.wait()
    assert (
        await service.request(event="push", repository="evex-dev/linejs", delivery_id="d2")
        == "scheduled"
    )
    gate.set()
    await service.wait_idle()

    assert len(indexing.calls) == 2
    assert service.runs == 2
    assert service.failures == 0


@pytest.mark.asyncio
async def test_failure_is_counted_without_raising() -> None:
    indexing = FakeIndexing()
    indexing.fail = True
    service = WebhookSyncService(indexing, _REPO, "evex-dev/linejs")  # type: ignore[arg-type]

    assert (
        await service.request(event="push", repository="evex-dev/linejs", delivery_id="d1")
        == "scheduled"
    )
    await service.wait_idle()
    assert service.failures == 1
    assert service.runs == 1


@pytest.mark.asyncio
async def test_stop_prevents_new_work() -> None:
    indexing = FakeIndexing()
    service = WebhookSyncService(indexing, _REPO, "evex-dev/linejs")  # type: ignore[arg-type]
    await service.stop()
    assert (
        await service.request(event="push", repository="evex-dev/linejs", delivery_id="d1")
        == "stopped"
    )
