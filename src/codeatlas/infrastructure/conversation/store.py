"""Thread-offloaded SQLAlchemy implementation of the conversation store port."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, create_engine, delete, event, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from codeatlas.domain.conversation_models import (
    ConversationId,
    ConversationMessage,
    InteractionClaim,
    InteractionClaimStatus,
    InteractionOutcomeStatus,
    MessageRole,
    MessageStatus,
)
from codeatlas.domain.errors import ConversationBusyError, ConversationStoreError
from codeatlas.infrastructure.conversation.models import (
    ConversationLockRow,
    ConversationMessageRow,
    ConversationRow,
    InteractionRecordRow,
)
from codeatlas.infrastructure.indexing.store import SCHEMA_REVISION

_IN_PROGRESS = "in_progress"
_COMPLETED = "completed"
_FAILED = "failed"
_REJECTED = "rejected"
_TERMINAL_STATUSES = frozenset({_COMPLETED, _FAILED, _REJECTED})


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ConversationStoreError(internal_cause=error) from error
    if parsed.tzinfo is None:
        raise ConversationStoreError()
    return parsed.astimezone(UTC)


class SqliteConversationStore:
    """Persist conversation memory, idempotency claims, and thread locks."""

    def __init__(
        self,
        database_url: str,
        *,
        busy_timeout_seconds: float = 5.0,
        claim_lease_seconds: float = 300.0,
        lock_poll_seconds: float = 0.01,
    ) -> None:
        if claim_lease_seconds <= 0:
            raise ValueError("claim lease must be positive")
        if lock_poll_seconds <= 0:
            raise ValueError("lock poll interval must be positive")
        self._claim_lease_seconds = claim_lease_seconds
        self._lock_poll_seconds = lock_poll_seconds
        self.engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False, "timeout": busy_timeout_seconds},
        )
        event.listen(self.engine, "connect", self._configure_connection)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _configure_connection(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.close()

    async def assert_schema_current(self) -> None:
        await asyncio.to_thread(self._assert_schema_current)

    def _assert_schema_current(self) -> None:
        try:
            with self.engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
                if revision != SCHEMA_REVISION:
                    raise ConversationStoreError()
        except ConversationStoreError:
            raise
        except SQLAlchemyError as error:
            raise ConversationStoreError(internal_cause=error) from error

    async def try_claim_interaction(
        self,
        *,
        idempotency_key: str,
        conversation_id: ConversationId,
        now: datetime,
    ) -> InteractionClaim:
        return await asyncio.to_thread(
            self._try_claim_interaction, idempotency_key, conversation_id, now
        )

    def _try_claim_interaction(
        self, idempotency_key: str, conversation_id: ConversationId, now: datetime
    ) -> InteractionClaim:
        now_iso = _utc_iso(now)
        expiry_iso = _utc_iso(now - timedelta(seconds=self._claim_lease_seconds))
        try:
            with self._sessions.begin() as session:
                self._touch_conversation(session, conversation_id.value, now_iso)
                inserted = session.execute(
                    sqlite_insert(InteractionRecordRow)
                    .values(
                        idempotency_key=idempotency_key,
                        conversation_id=conversation_id.value,
                        status=_IN_PROGRESS,
                        response_text=None,
                        claimed_at=now_iso,
                        updated_at=now_iso,
                    )
                    .on_conflict_do_nothing(index_elements=["idempotency_key"])
                ).rowcount
                if inserted:
                    return InteractionClaim(InteractionClaimStatus.CLAIMED)
                row = session.get(InteractionRecordRow, idempotency_key)
                if row is None:
                    raise ConversationStoreError()
                if row.status == _IN_PROGRESS:
                    reclaimed = session.execute(
                        update(InteractionRecordRow)
                        .where(
                            InteractionRecordRow.idempotency_key == idempotency_key,
                            InteractionRecordRow.status == _IN_PROGRESS,
                            InteractionRecordRow.claimed_at < expiry_iso,
                        )
                        .values(claimed_at=now_iso, updated_at=now_iso)
                    ).rowcount
                    if reclaimed:
                        return InteractionClaim(InteractionClaimStatus.CLAIMED)
                    return InteractionClaim(InteractionClaimStatus.ALREADY_IN_PROGRESS)
                return self._terminal_claim(row)
        except ConversationStoreError:
            raise
        except SQLAlchemyError as error:
            raise ConversationStoreError(internal_cause=error) from error

    @staticmethod
    def _terminal_claim(row: InteractionRecordRow) -> InteractionClaim:
        try:
            if row.status == _COMPLETED:
                return InteractionClaim(
                    InteractionClaimStatus.ALREADY_COMPLETED, prior_response=row.response_text
                )
            if row.status in _TERMINAL_STATUSES:
                return InteractionClaim(
                    InteractionClaimStatus.ALREADY_FAILED, prior_response=row.response_text
                )
            raise ConversationStoreError()
        except ValueError as error:
            raise ConversationStoreError(internal_cause=error) from error

    async def complete_interaction(
        self,
        *,
        idempotency_key: str,
        status: InteractionOutcomeStatus,
        text: str | None,
        now: datetime,
    ) -> None:
        await asyncio.to_thread(self._complete_interaction, idempotency_key, status, text, now)

    def _complete_interaction(
        self,
        idempotency_key: str,
        status: InteractionOutcomeStatus,
        text: str | None,
        now: datetime,
    ) -> None:
        if not isinstance(status, InteractionOutcomeStatus):
            raise ValueError("invalid interaction outcome status")
        now_iso = _utc_iso(now)
        try:
            with self._sessions.begin() as session:
                changed = session.execute(
                    update(InteractionRecordRow)
                    .where(
                        InteractionRecordRow.idempotency_key == idempotency_key,
                        InteractionRecordRow.status == _IN_PROGRESS,
                    )
                    .values(status=status.value, response_text=text, updated_at=now_iso)
                ).rowcount
                if changed:
                    return
                if session.get(InteractionRecordRow, idempotency_key) is None:
                    raise ConversationStoreError()
        except ConversationStoreError:
            raise
        except SQLAlchemyError as error:
            raise ConversationStoreError(internal_cause=error) from error

    async def get_interaction_response(
        self, idempotency_key: str
    ) -> tuple[InteractionOutcomeStatus, str | None] | None:
        return await asyncio.to_thread(self._get_interaction_response, idempotency_key)

    def _get_interaction_response(
        self, idempotency_key: str
    ) -> tuple[InteractionOutcomeStatus, str | None] | None:
        try:
            with self._sessions() as session:
                row = session.get(InteractionRecordRow, idempotency_key)
                if row is None or row.status == _IN_PROGRESS:
                    return None
                try:
                    status = InteractionOutcomeStatus(row.status)
                except ValueError as error:
                    raise ConversationStoreError(internal_cause=error) from error
                return status, row.response_text
        except ConversationStoreError:
            raise
        except SQLAlchemyError as error:
            raise ConversationStoreError(internal_cause=error) from error

    async def append_message(self, message: ConversationMessage) -> None:
        await asyncio.to_thread(self._append_message, message)

    def _append_message(self, message: ConversationMessage) -> None:
        created_iso = _utc_iso(message.created_at)
        try:
            with self._sessions.begin() as session:
                self._touch_conversation(session, message.conversation_id.value, created_iso)
                session.add(
                    ConversationMessageRow(
                        conversation_id=message.conversation_id.value,
                        role=message.role.value,
                        content=message.content,
                        status=message.status.value,
                        source_message_id=message.source_message_id,
                        correlation_id=message.correlation_id,
                        created_at=created_iso,
                    )
                )
        except SQLAlchemyError as error:
            raise ConversationStoreError(internal_cause=error) from error

    async def load_history(
        self, conversation_id: ConversationId, limit: int
    ) -> tuple[ConversationMessage, ...]:
        return await asyncio.to_thread(self._load_history, conversation_id, limit)

    def _load_history(
        self, conversation_id: ConversationId, limit: int
    ) -> tuple[ConversationMessage, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("invalid conversation history limit")
        try:
            with self._sessions() as session:
                rows = list(
                    session.scalars(
                        select(ConversationMessageRow)
                        .where(ConversationMessageRow.conversation_id == conversation_id.value)
                        .order_by(ConversationMessageRow.message_id.desc())
                        .limit(limit)
                    ).all()
                )
                rows.reverse()
                return tuple(self._message_from_row(row) for row in rows)
        except ConversationStoreError:
            raise
        except SQLAlchemyError as error:
            raise ConversationStoreError(internal_cause=error) from error

    @staticmethod
    def _message_from_row(row: ConversationMessageRow) -> ConversationMessage:
        try:
            return ConversationMessage(
                conversation_id=ConversationId(row.conversation_id),
                role=MessageRole(row.role),
                content=row.content,
                created_at=_parse_timestamp(row.created_at),
                source_message_id=row.source_message_id,
                status=MessageStatus(row.status),
                correlation_id=row.correlation_id,
            )
        except ValueError as error:
            raise ConversationStoreError(internal_cause=error) from error

    async def acquire_conversation_lock(
        self,
        *,
        conversation_id: ConversationId,
        owner_token: str,
        wait_seconds: float,
        lease_seconds: float,
        now: datetime,
    ) -> None:
        if wait_seconds < 0:
            raise ValueError("lock wait must not be negative")
        if lease_seconds <= 0:
            raise ValueError("lock lease must be positive")
        if not owner_token or len(owner_token) > 200:
            raise ValueError("invalid lock owner token")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_seconds
        while True:
            if await asyncio.to_thread(
                self._try_acquire_lock, conversation_id, owner_token, lease_seconds, now
            ):
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ConversationBusyError()
            await asyncio.sleep(min(self._lock_poll_seconds, remaining))

    def _try_acquire_lock(
        self,
        conversation_id: ConversationId,
        owner_token: str,
        lease_seconds: float,
        now: datetime,
    ) -> bool:
        now_iso = _utc_iso(now)
        expires_iso = _utc_iso(now + timedelta(seconds=lease_seconds))
        try:
            with self._sessions.begin() as session:
                self._touch_conversation(session, conversation_id.value, now_iso)
                inserted = session.execute(
                    sqlite_insert(ConversationLockRow)
                    .values(
                        conversation_id=conversation_id.value,
                        owner_token=owner_token,
                        acquired_at=now_iso,
                        expires_at=expires_iso,
                        heartbeat_at=now_iso,
                    )
                    .on_conflict_do_nothing(index_elements=["conversation_id"])
                ).rowcount
                if inserted:
                    return True
                reentered = session.execute(
                    update(ConversationLockRow)
                    .where(
                        ConversationLockRow.conversation_id == conversation_id.value,
                        ConversationLockRow.owner_token == owner_token,
                    )
                    .values(expires_at=expires_iso, heartbeat_at=now_iso)
                ).rowcount
                if reentered:
                    return True
                taken_over = session.execute(
                    update(ConversationLockRow)
                    .where(
                        ConversationLockRow.conversation_id == conversation_id.value,
                        ConversationLockRow.expires_at <= now_iso,
                    )
                    .values(
                        owner_token=owner_token,
                        acquired_at=now_iso,
                        expires_at=expires_iso,
                        heartbeat_at=now_iso,
                    )
                ).rowcount
                return bool(taken_over)
        except SQLAlchemyError as error:
            raise ConversationStoreError(internal_cause=error) from error

    async def release_conversation_lock(
        self, *, conversation_id: ConversationId, owner_token: str
    ) -> None:
        await asyncio.to_thread(self._release_conversation_lock, conversation_id, owner_token)

    def _release_conversation_lock(self, conversation_id: ConversationId, owner_token: str) -> None:
        try:
            with self._sessions.begin() as session:
                session.execute(
                    delete(ConversationLockRow).where(
                        ConversationLockRow.conversation_id == conversation_id.value,
                        ConversationLockRow.owner_token == owner_token,
                    )
                )
        except SQLAlchemyError as error:
            raise ConversationStoreError(internal_cause=error) from error

    async def purge_history_before(self, cutoff: datetime) -> int:
        return await asyncio.to_thread(self._purge_history_before, cutoff)

    def _purge_history_before(self, cutoff: datetime) -> int:
        cutoff_iso = _utc_iso(cutoff)
        try:
            with self._sessions.begin() as session:
                removed = session.execute(
                    delete(ConversationRow).where(ConversationRow.updated_at < cutoff_iso)
                ).rowcount
                return int(removed)
        except SQLAlchemyError as error:
            raise ConversationStoreError(internal_cause=error) from error

    async def close(self) -> None:
        await asyncio.to_thread(self._close)

    def _close(self) -> None:
        try:
            self.engine.dispose()
        except SQLAlchemyError as error:
            raise ConversationStoreError(internal_cause=error) from error

    @staticmethod
    def _touch_conversation(session: Session, conversation_id: str, activity_iso: str) -> None:
        session.execute(
            sqlite_insert(ConversationRow)
            .values(
                conversation_id=conversation_id,
                created_at=activity_iso,
                updated_at=activity_iso,
            )
            .on_conflict_do_nothing(index_elements=["conversation_id"])
        )
        session.execute(
            update(ConversationRow)
            .where(ConversationRow.conversation_id == conversation_id)
            .values(
                updated_at=case(
                    (ConversationRow.updated_at < activity_iso, activity_iso),
                    else_=ConversationRow.updated_at,
                )
            )
        )
