"""SQLAlchemy persistence models for conversation memory; these are not domain models."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for the M5 conversation schema."""


class ConversationRow(Base):
    __tablename__ = "conversations"
    conversation_id: Mapped[str] = mapped_column(String(37), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, index=True)


class ConversationMessageRow(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')", name="ck_conversation_messages_role"),
        CheckConstraint(
            "status IN ('ok','partial','error','rejected')",
            name="ck_conversation_messages_status",
        ),
        Index("ix_conversation_messages_history", "conversation_id", "message_id"),
        Index("ix_conversation_messages_created", "created_at"),
    )
    message_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_message_id: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class InteractionRecordRow(Base):
    __tablename__ = "interaction_records"
    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress','completed','failed','rejected')",
            name="ck_interaction_records_status",
        ),
        Index("ix_interaction_records_conversation", "conversation_id"),
        Index("ix_interaction_records_updated", "updated_at"),
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    response_text: Mapped[str | None] = mapped_column(Text)
    claimed_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ConversationLockRow(Base):
    __tablename__ = "conversation_locks"
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"), primary_key=True
    )
    owner_token: Mapped[str] = mapped_column(String(200), nullable=False)
    acquired_at: Mapped[str] = mapped_column(String(40), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    heartbeat_at: Mapped[str] = mapped_column(String(40), nullable=False)
