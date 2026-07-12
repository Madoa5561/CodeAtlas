"""Create the M3 transactional indexing schema."""

from __future__ import annotations

from alembic import op
from sqlalchemy import insert

from codeatlas.infrastructure.indexing.models import Base, SchemaMetadataRow

revision = "0001_m3_transactional_index"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create all M3 tables, constraints, and search indexes."""
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    bind.execute(
        insert(SchemaMetadataRow).values(key="storage_source_of_truth", value="sqlite-m3-v1")
    )


def downgrade() -> None:
    """Remove the complete M3 schema; this is never run automatically."""
    Base.metadata.drop_all(bind=op.get_bind())
