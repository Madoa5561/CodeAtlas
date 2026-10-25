"""SQLite models for GitHub webhook delivery idempotency."""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WebhookBase(DeclarativeBase):
    """Separate metadata keeps webhook schema evolution independent."""


class WebhookDeliveryRow(WebhookBase):
    """One verified GitHub webhook delivery identity."""

    __tablename__ = "webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str | None] = mapped_column(String(64))
    repository: Mapped[str | None] = mapped_column(String(255))
    received_at: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="accepted")
