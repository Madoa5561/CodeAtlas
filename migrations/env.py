"""Alembic environment for the explicit CodeAtlas SQLite migration workflow."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from codeatlas.infrastructure.conversation.models import Base as ConversationBase
from codeatlas.infrastructure.indexing.models import Base as IndexBase
from codeatlas.infrastructure.webhook.models import WebhookBase

config = context.config
target_metadata = [IndexBase.metadata, ConversationBase.metadata, WebhookBase.metadata]


def run_migrations_offline() -> None:
    """Generate SQL without opening a database connection."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the explicitly configured database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
