"""Coalesced background incremental indexing for GitHub push webhooks."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from codeatlas.application.indexing import TransactionalIndexingService
from codeatlas.domain.indexing_models import IndexingRequest
from codeatlas.domain.repository_models import RepositoryId

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WebhookSyncService:
    """Turn verified push webhooks into serialized incremental index runs."""

    indexing: TransactionalIndexingService
    repository_id: RepositoryId
    repository_full_name: str
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _task: asyncio.Task[None] | None = field(default=None, repr=False)
    _pending: bool = field(default=False, repr=False)
    _runs: int = 0
    _failures: int = 0
    _stopped: bool = False

    @property
    def runs(self) -> int:
        """Return the number of completed index attempts."""
        return self._runs

    @property
    def failures(self) -> int:
        """Return the number of failed index attempts."""
        return self._failures

    async def request(
        self,
        *,
        event: str,
        repository: str | None,
        delivery_id: str,
    ) -> str:
        """Schedule one coalesced incremental index run for a push event."""
        if self._stopped:
            return "stopped"
        if event != "push":
            return "ignored"
        if repository is None or repository.casefold() != self.repository_full_name.casefold():
            return "ignored"
        if self._task is not None and not self._task.done():
            self._pending = True
            return "scheduled"
        self._task = asyncio.create_task(self._run(delivery_id), name="codeatlas-webhook-sync")
        return "scheduled"

    async def _run(self, delivery_id: str) -> None:
        await self._drain(delivery_id)
        # A request may arrive between the final pending check and task exit.
        if self._pending and not self._stopped:
            self._pending = False
            self._task = asyncio.create_task(self._run(delivery_id), name="codeatlas-webhook-sync")

    async def _drain(self, delivery_id: str) -> None:
        async with self._lock:
            while not self._stopped:
                self._pending = False
                self._runs += 1
                try:
                    result = await self.indexing.create_index(
                        IndexingRequest(
                            self.repository_id,
                            "HEAD",
                            incremental=True,
                        )
                    )
                    logger.info(
                        "webhook_index_completed",
                        extra={
                            "delivery_id": delivery_id,
                            "generation_id": result.snapshot.generation_id.value,
                            "status": result.status.value,
                        },
                    )
                except Exception:
                    self._failures += 1
                    logger.exception(
                        "webhook_index_failed",
                        extra={"delivery_id": delivery_id},
                    )
                if not self._pending:
                    break

    async def wait_idle(self) -> None:
        """Wait for the active run and any immediate follow-up."""
        while True:
            task = self._task
            if task is None:
                return
            try:
                await task
            except asyncio.CancelledError:
                return
            if self._task is task:
                self._task = None
            if self._task is None:
                return

    async def stop(self) -> None:
        """Cancel pending indexing work during shutdown."""
        self._stopped = True
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
