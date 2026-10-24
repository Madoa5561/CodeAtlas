"""Create the M4/M5 conversation memory and idempotency schema."""

from __future__ import annotations

from alembic import op
from sqlalchemy import delete, insert

from codeatlas.infrastructure.conversation.models import Base as ConversationBase
from codeatlas.infrastructure.indexing.models import SchemaMetadataRow

revision = "0002_m4_conversation"
down_revision = "0001_m3_transactional_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create conversation, message, interaction, and lock tables."""
    bind = op.get_bind()
    ConversationBase.metadata.create_all(bind=bind)
    bind.execute(
        insert(SchemaMetadataRow).values(key="conversation_source_of_truth", value="sqlite-m5-v1")
    )


def downgrade() -> None:
    """Remove conversation tables while preserving M3 index data."""
    bind = op.get_bind()
    ConversationBase.metadata.drop_all(bind=bind)
    bind.execute(
        delete(SchemaMetadataRow).where(SchemaMetadataRow.key == "conversation_source_of_truth")
    )
