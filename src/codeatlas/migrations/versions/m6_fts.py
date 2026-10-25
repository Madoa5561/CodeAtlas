"""Create the M6 FTS5 lexical search projection."""

from __future__ import annotations

from alembic import op
from sqlalchemy import delete, insert

from codeatlas.infrastructure.indexing.fts import (
    FTS_SCHEMA_VERSION,
    backfill_fts,
    create_fts_schema,
    drop_fts_schema,
)
from codeatlas.infrastructure.indexing.models import SchemaMetadataRow

revision = "0003_m6_fts"
down_revision = "0002_m4_conversation"
branch_labels = None
depends_on = None

_METADATA_KEY = "lexical_projection"


def upgrade() -> None:
    """Create and backfill the line-level FTS5 table from safe stored content."""
    bind = op.get_bind()
    create_fts_schema(bind)
    backfill_fts(bind)
    bind.execute(
        insert(SchemaMetadataRow).values(
            key=_METADATA_KEY,
            value=FTS_SCHEMA_VERSION,
        )
    )


def downgrade() -> None:
    """Remove the lexical projection while preserving the source index."""
    bind = op.get_bind()
    drop_fts_schema(bind)
    bind.execute(delete(SchemaMetadataRow).where(SchemaMetadataRow.key == _METADATA_KEY))
