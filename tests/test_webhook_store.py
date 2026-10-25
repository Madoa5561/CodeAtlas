"""Idempotency and schema tests for the webhook delivery ledger."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeatlas.domain.errors import ConversationStoreError
from codeatlas.infrastructure.indexing.migrations import upgrade_database
from codeatlas.infrastructure.webhook.store import SqliteWebhookStore


def make_store(tmp_path: Path) -> tuple[SqliteWebhookStore, str]:
    url = f"sqlite:///{(tmp_path / 'webhook.db').as_posix()}"
    upgrade_database(url)
    return SqliteWebhookStore(url), url


@pytest.mark.asyncio
async def test_delivery_is_recorded_once(tmp_path: Path) -> None:
    """A repeated GitHub delivery ID is idempotent."""
    store, _ = make_store(tmp_path)
    try:
        first = await store.record_delivery(
            delivery_id="delivery-1",
            event="push",
            action=None,
            repository="evex-dev/linejs",
        )
        second = await store.record_delivery(
            delivery_id="delivery-1",
            event="push",
            action=None,
            repository="evex-dev/linejs",
        )
    finally:
        await store.close()

    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_webhook_schema_mismatch_fails_safely(tmp_path: Path) -> None:
    """A stale database is rejected with a safe error."""
    url = f"sqlite:///{(tmp_path / 'stale.db').as_posix()}"
    store = SqliteWebhookStore(url)
    try:
        with pytest.raises(ConversationStoreError):
            await store.assert_schema_current()
    finally:
        await store.close()
