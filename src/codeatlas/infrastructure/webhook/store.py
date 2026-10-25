"""Durable idempotency ledger for verified GitHub webhook deliveries."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from codeatlas.domain.errors import ConversationStoreError
from codeatlas.infrastructure.indexing.store import SCHEMA_REVISION


class SqliteWebhookStore:
    """Record webhook delivery identifiers without storing payload content."""

    def __init__(self, database_url: str, *, busy_timeout_seconds: float = 5.0) -> None:
        self._engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False, "timeout": busy_timeout_seconds},
        )
        event.listen(self._engine, "connect", self._configure_connection)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    @staticmethod
    def _configure_connection(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    async def assert_schema_current(self) -> None:
        """Fail safely unless the packaged migration head is applied."""
        try:
            await asyncio.to_thread(self._assert_schema_current)
        except ConversationStoreError:
            raise
        except Exception as error:
            raise ConversationStoreError(internal_cause=error) from error

    def _assert_schema_current(self) -> None:
        with self._engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version"))
            if revision.scalar_one_or_none() != SCHEMA_REVISION:
                raise ConversationStoreError("The webhook schema is not current.")

    async def record_delivery(
        self,
        *,
        delivery_id: str,
        event: str,
        action: str | None,
        repository: str | None,
    ) -> bool:
        """Insert a delivery ID and return False when it was already recorded."""
        return await asyncio.to_thread(
            self._record_delivery,
            delivery_id,
            event,
            action,
            repository,
        )

    def _record_delivery(
        self,
        delivery_id: str,
        event: str,
        action: str | None,
        repository: str | None,
    ) -> bool:
        try:
            with self._sessions.begin() as session:
                result = cast(
                    CursorResult[Any],
                    session.execute(
                        text(
                            "INSERT INTO webhook_deliveries "
                            "(delivery_id, event, action, repository, received_at, status) "
                            "VALUES (:delivery_id, :event, :action, :repository, :received_at, "
                            "'accepted') ON CONFLICT(delivery_id) DO NOTHING"
                        ),
                        {
                            "delivery_id": delivery_id[:128],
                            "event": event[:64],
                            "action": action[:64] if action else None,
                            "repository": repository[:255] if repository else None,
                            "received_at": datetime.now(UTC).isoformat(),
                        },
                    ),
                )
                return int(result.rowcount) == 1
        except SQLAlchemyError as error:
            raise ConversationStoreError(internal_cause=error) from error

    async def close(self) -> None:
        """Release the database engine; repeated calls are safe."""
        await asyncio.to_thread(self._engine.dispose)
