"""Create the M6 GitHub webhook delivery ledger."""

from __future__ import annotations

from alembic import op
from sqlalchemy import insert

from codeatlas.infrastructure.indexing.models import SchemaMetadataRow
from codeatlas.infrastructure.webhook.models import WebhookBase

revision = "0004_m6_webhook"
down_revision = "0003_m6_fts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the webhook idempotency table."""
    bind = op.get_bind()
    WebhookBase.metadata.create_all(bind=bind)
    bind.execute(
        insert(SchemaMetadataRow).values(key="webhook_source_of_truth", value="sqlite-webhook-v1")
    )


def downgrade() -> None:
    """Remove webhook delivery records while preserving code indices."""
    bind = op.get_bind()
    WebhookBase.metadata.drop_all(bind=bind)
    bind.exec_driver_sql("DELETE FROM schema_metadata WHERE key = 'webhook_source_of_truth'")
