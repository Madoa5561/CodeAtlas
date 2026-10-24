"""Channel-independent interaction use case with idempotency and thread locking."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from codeatlas.domain.agent_models import AgentLimits, QueryContext
from codeatlas.domain.agent_ports import AgentRunnerPort
from codeatlas.domain.conversation_models import (
    ConversationId,
    ConversationMessage,
    InteractionClaimStatus,
    InteractionOutcomeStatus,
    MessageRole,
    MessageStatus,
)
from codeatlas.domain.conversation_ports import ConversationStorePort
from codeatlas.domain.errors import CodeAtlasError, ConversationBusyError
from codeatlas.domain.interaction_models import (
    CanonicalInteraction,
    CanonicalResponse,
    ResponseBlock,
    ResponseBlockKind,
    ResponseStatus,
)

logger = logging.getLogger(__name__)

_SAFE_FAILURE_MESSAGE = (
    "回答の生成中に問題が発生したため、この質問には回答できませんでした。"
    "時間をおいて再試行してください。"
)
_SAFE_BUSY_MESSAGE = "このスレッドでは別の質問を処理中です。処理が完了してから再送してください。"
_SAFE_UNAVAILABLE_MESSAGE = "質問応答機能は現在利用できません。"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class InteractionOutcome:
    """Result of one interaction, including duplicate and safe-failure states."""

    processed: bool
    duplicate: bool
    conversation_id: ConversationId
    claim_status: InteractionClaimStatus
    response: CanonicalResponse | None = None
    prior_response_text: str | None = None
    diagnostics: dict[str, str] = field(default_factory=dict)


def safe_error_response(
    message: str, *, status: ResponseStatus = ResponseStatus.ERROR
) -> CanonicalResponse:
    """Build a citation-free response that never leaks internal diagnostics."""
    return CanonicalResponse(
        status=status,
        blocks=(ResponseBlock(kind=ResponseBlockKind.TEXT, text=message),),
        citations=(),
        confidence=0.0,
        uncertainties=(),
        safe_error=message,
    )


class InteractionApplicationService:
    """Execute one canonical interaction through memory, locking, and the agent."""

    def __init__(
        self,
        *,
        store: ConversationStorePort,
        agent: AgentRunnerPort,
        limits: AgentLimits,
        history_limit: int = 12,
        lock_wait_seconds: float = 20.0,
        lock_lease_seconds: float = 300.0,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not 1 <= history_limit <= 100:
            raise ValueError("invalid conversation history limit")
        if not 0.1 <= lock_wait_seconds <= 300.0:
            raise ValueError("invalid conversation lock wait")
        if not 30.0 <= lock_lease_seconds <= 3_600.0:
            raise ValueError("invalid conversation lock lease")
        self._store = store
        self._agent = agent
        self._limits = limits
        self._history_limit = history_limit
        self._lock_wait_seconds = lock_wait_seconds
        self._lock_lease_seconds = lock_lease_seconds
        self._clock = clock

    async def handle(self, interaction: CanonicalInteraction) -> InteractionOutcome:
        """Process an interaction at most once per idempotency key."""
        conversation_id = ConversationId.derive(
            interaction.conversation.source,
            interaction.conversation.conversation_id,
        )
        now = self._clock()
        claim = await self._store.try_claim_interaction(
            idempotency_key=interaction.idempotency_key,
            conversation_id=conversation_id,
            now=now,
        )
        if not claim.should_process:
            logger.info(
                "interaction_duplicate",
                extra={"claim_status": claim.status.value, "kind": interaction.kind.value},
            )
            return InteractionOutcome(
                processed=False,
                duplicate=True,
                conversation_id=conversation_id,
                claim_status=claim.status,
                prior_response_text=claim.prior_response,
            )

        owner_token = secrets.token_hex(16)
        try:
            await self._store.acquire_conversation_lock(
                conversation_id=conversation_id,
                owner_token=owner_token,
                wait_seconds=self._lock_wait_seconds,
                lease_seconds=self._lock_lease_seconds,
                now=now,
            )
        except ConversationBusyError:
            logger.warning("interaction_conversation_busy", extra={"kind": interaction.kind.value})
            response = safe_error_response(_SAFE_BUSY_MESSAGE, status=ResponseStatus.REJECTED)
            await self._finish(
                interaction,
                conversation_id,
                response,
                InteractionOutcomeStatus.REJECTED,
                now=self._clock(),
            )
            return InteractionOutcome(
                processed=True,
                duplicate=False,
                conversation_id=conversation_id,
                claim_status=claim.status,
                response=response,
            )
        except Exception:
            logger.exception("interaction_lock_failed")
            response = safe_error_response(_SAFE_FAILURE_MESSAGE)
            await self._finish(
                interaction,
                conversation_id,
                response,
                InteractionOutcomeStatus.FAILED,
                now=self._clock(),
            )
            return InteractionOutcome(
                processed=True,
                duplicate=False,
                conversation_id=conversation_id,
                claim_status=claim.status,
                response=response,
            )

        try:
            return await self._run_agent(interaction, conversation_id, claim.status)
        finally:
            try:
                await self._store.release_conversation_lock(
                    conversation_id=conversation_id,
                    owner_token=owner_token,
                )
            except Exception:
                logger.exception("interaction_unlock_failed")

    async def _run_agent(
        self,
        interaction: CanonicalInteraction,
        conversation_id: ConversationId,
        claim_status: InteractionClaimStatus,
    ) -> InteractionOutcome:
        try:
            history = await self._store.load_history(conversation_id, self._history_limit)
            user_message = ConversationMessage(
                conversation_id=conversation_id,
                role=MessageRole.USER,
                content=interaction.message.content,
                created_at=interaction.message.created_at,
                source_message_id=interaction.message.message_id,
                correlation_id=interaction.idempotency_key,
            )
            await self._store.append_message(user_message)

            context = QueryContext(
                request_id=interaction.interaction_id,
                query=interaction.message.content,
                conversation_id=conversation_id,
                capabilities=interaction.capabilities,
                locale=interaction.locale,
                history=history,
                received_at=interaction.received_at,
            )
            outcome = await self._agent.run(context, self._limits)
            response = outcome.response
            await self._finish(
                interaction,
                conversation_id,
                response,
                InteractionOutcomeStatus.COMPLETED,
                now=self._clock(),
            )
            logger.info(
                "interaction_completed",
                extra={
                    "status": response.status.value,
                    "iterations": outcome.diagnostics.iterations,
                    "tool_calls": outcome.diagnostics.tool_calls,
                    "evidence": outcome.diagnostics.evidence_ranked,
                },
            )
            return InteractionOutcome(
                processed=True,
                duplicate=False,
                conversation_id=conversation_id,
                claim_status=claim_status,
                response=response,
            )
        except CodeAtlasError as error:
            logger.warning(
                "interaction_failed",
                extra={"error_code": error.code},
            )
            response = safe_error_response(error.safe_message)
            await self._finish(
                interaction,
                conversation_id,
                response,
                InteractionOutcomeStatus.FAILED,
                now=self._clock(),
            )
            return InteractionOutcome(
                processed=True,
                duplicate=False,
                conversation_id=conversation_id,
                claim_status=claim_status,
                response=response,
            )
        except Exception:
            logger.exception("interaction_unexpected_failure")
            response = safe_error_response(_SAFE_FAILURE_MESSAGE)
            await self._finish(
                interaction,
                conversation_id,
                response,
                InteractionOutcomeStatus.FAILED,
                now=self._clock(),
            )
            return InteractionOutcome(
                processed=True,
                duplicate=False,
                conversation_id=conversation_id,
                claim_status=claim_status,
                response=response,
            )

    async def _finish(
        self,
        interaction: CanonicalInteraction,
        conversation_id: ConversationId,
        response: CanonicalResponse,
        status: InteractionOutcomeStatus,
        *,
        now: datetime,
    ) -> None:
        message_status = (
            MessageStatus.OK
            if response.status is ResponseStatus.OK
            else MessageStatus.PARTIAL
            if response.status is ResponseStatus.PARTIAL
            else MessageStatus.REJECTED
            if response.status is ResponseStatus.REJECTED
            else MessageStatus.ERROR
        )
        try:
            await self._store.append_message(
                ConversationMessage(
                    conversation_id=conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=response.plain_text,
                    created_at=now,
                    status=message_status,
                    correlation_id=interaction.idempotency_key,
                )
            )
        except CodeAtlasError:
            logger.warning("conversation_assistant_message_not_stored")
        await self._store.complete_interaction(
            idempotency_key=interaction.idempotency_key,
            status=status,
            text=response.plain_text,
            now=now,
        )
