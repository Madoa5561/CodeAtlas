"""Restricted administrative CLI for M3 migration and indexing verification."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from codeatlas import API_VERSION
from codeatlas.adapters.embedding import build_embedding_provider
from codeatlas.adapters.parsing.tree_sitter_parser import TreeSitterTypeScriptParser
from codeatlas.adapters.parsing.tree_sitter_plugin import TypeScriptJavaScriptParserPlugin
from codeatlas.adapters.repository.plugin import LocalGitRepositoryPlugin
from codeatlas.adapters.vector import build_vector_index
from codeatlas.application.indexing import IndexingPolicy, TransactionalIndexingService
from codeatlas.application.vector_projection import VectorProjector
from codeatlas.config.settings import CodeAtlasSettings, load_settings
from codeatlas.domain.indexing_models import IndexGenerationId, IndexingRequest
from codeatlas.domain.repository_models import RepositoryId, RepositoryLocation
from codeatlas.infrastructure.conversation.store import SqliteConversationStore
from codeatlas.infrastructure.indexing.migrations import (
    current_revision,
    downgrade_database,
    upgrade_database,
)
from codeatlas.infrastructure.indexing.query import SqliteIndexQuery
from codeatlas.infrastructure.indexing.store import SqliteIndexStore
from codeatlas.plugin_api.registry import PluginRegistry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codeatlas-admin")
    parser.add_argument("--json", action="store_true", dest="json_output")
    subcommands = parser.add_subparsers(dest="command", required=True)
    db = subcommands.add_parser("db")
    db_commands = db.add_subparsers(dest="db_command", required=True)
    db_commands.add_parser("upgrade")
    db_commands.add_parser("current")
    downgrade = db_commands.add_parser("downgrade")
    downgrade.add_argument("--revision", default="base")
    index = subcommands.add_parser("index")
    index_commands = index.add_subparsers(dest="index_command", required=True)
    for name in ("full", "incremental"):
        command = index_commands.add_parser(name)
        command.add_argument("repository", type=Path)
        command.add_argument("revision")
    for name in ("status", "validate", "activate"):
        command = index_commands.add_parser(name)
        command.add_argument("generation_id")
    listing = index_commands.add_parser("list")
    listing.add_argument("repository_id")
    rollback = index_commands.add_parser("rollback")
    rollback.add_argument("repository_id")
    rollback.add_argument("generation_id")
    stale = index_commands.add_parser("mark-stale")
    stale.add_argument("--before", required=True)
    conversations = subcommands.add_parser("conversations")
    conversation_commands = conversations.add_subparsers(dest="conversation_command", required=True)
    purge = conversation_commands.add_parser("purge")
    purge.add_argument("--days", type=int, default=None)
    vector = subcommands.add_parser("vector")
    vector_commands = vector.add_subparsers(dest="vector_command", required=True)
    for name in ("rebuild", "status"):
        command = vector_commands.add_parser(name)
        command.add_argument("repository", type=Path)
    return parser


def _service(settings: CodeAtlasSettings) -> TransactionalIndexingService:
    repository_plugin = LocalGitRepositoryPlugin.from_settings(settings.repository)
    parser_plugin = TypeScriptJavaScriptParserPlugin(
        parser=TreeSitterTypeScriptParser(
            max_source_bytes=settings.indexing.max_file_bytes,
            max_nodes=settings.indexing.max_parser_nodes,
            max_depth=settings.indexing.max_parser_depth,
        )
    )
    registry = PluginRegistry(
        api_version=API_VERSION,
        allowlist=settings.security.plugin_allowlist,
    )
    registry.register(repository_plugin)
    registry.register(parser_plugin)
    policy = IndexingPolicy(
        max_repository_files=settings.indexing.max_repository_files,
        max_concurrency=settings.indexing.max_concurrency,
        operation_timeout_seconds=settings.indexing.operation_timeout_seconds,
        lease_timeout_seconds=settings.database.lease_timeout_seconds,
        lock_wait_timeout_seconds=settings.database.lock_wait_timeout_seconds,
        max_failure_rate=settings.indexing.max_failure_rate,
        exclude_generated=settings.indexing.exclude_generated,
        exclude_minified=settings.indexing.exclude_minified,
    )
    return TransactionalIndexingService(
        repository_plugin.provider,
        SqliteIndexStore(
            settings.database.url,
            busy_timeout_seconds=settings.database.busy_timeout_seconds,
        ),
        (parser_plugin.parser,),
        policy,
    )


async def _run_index(args: argparse.Namespace, settings: CodeAtlasSettings) -> object:
    service = _service(settings)
    if args.index_command in {"full", "incremental"}:
        descriptor = await service.repository.register_local_repository(
            RepositoryLocation(args.repository)
        )
        return await service.create_index(
            IndexingRequest(
                descriptor.repository_id,
                args.revision,
                incremental=args.index_command == "incremental",
            )
        )
    generation_id = (
        IndexGenerationId(args.generation_id) if hasattr(args, "generation_id") else None
    )
    if args.index_command == "status" and generation_id:
        return {"status": (await service.get_generation_status(generation_id)).value}
    if args.index_command == "list":
        return await service.list_generations(RepositoryId(args.repository_id))
    if args.index_command == "validate" and generation_id:
        return await service.validate_generation(generation_id)
    if args.index_command == "activate" and generation_id:
        await service.activate_generation(generation_id)
        return {"status": "active"}
    if args.index_command == "rollback" and generation_id:
        await service.rollback_active(RepositoryId(args.repository_id), generation_id)
        return {"status": "active"}
    if args.index_command == "mark-stale":
        before = datetime.fromisoformat(args.before).astimezone(UTC)
        return {"failed_jobs": await service.mark_stale_jobs_failed(before)}
    raise ValueError("unsupported index command")


async def _run_conversations(args: argparse.Namespace, settings: CodeAtlasSettings) -> object:
    store = SqliteConversationStore(
        settings.database.url,
        busy_timeout_seconds=settings.database.busy_timeout_seconds,
        claim_lease_seconds=settings.conversation.thread_lock_lease_seconds,
    )
    try:
        await store.assert_schema_current()
        days = args.days if args.days is not None else settings.conversation.retention_days
        if days < 1:
            raise ValueError("retention days must be positive")
        cutoff = datetime.now(UTC) - timedelta(days=days)
        return {"purged_conversations": await store.purge_history_before(cutoff)}
    finally:
        await store.close()


async def _run_vector(args: argparse.Namespace, settings: CodeAtlasSettings) -> object:
    if not settings.embedding.enabled or not settings.qdrant.enabled:
        raise ValueError("embedding and qdrant must both be enabled")
    if current_revision(settings.database.url) is None:
        raise ValueError("the index schema is not initialized")
    repository_plugin = LocalGitRepositoryPlugin.from_settings(settings.repository)
    descriptor = await repository_plugin.provider.register_local_repository(
        RepositoryLocation(args.repository)
    )
    query = SqliteIndexQuery(settings.database.url)
    embedding = build_embedding_provider(settings.embedding)
    index = build_vector_index(settings.qdrant, dimensions=embedding.dimensions)
    try:
        if args.vector_command == "rebuild":
            projector = VectorProjector(query, embedding, index)
            stats = await projector.rebuild(descriptor.repository_id)
            return asdict(stats)
        snapshot = await query.get_active_snapshot(descriptor.repository_id)
        if snapshot is None:
            return {"fresh": False, "points": 0, "generation_id": None}
        started = time.monotonic()
        points = await index.count(
            repository_id=descriptor.repository_id,
            generation_id=snapshot.generation_id,
        )
        latency_ms = (time.monotonic() - started) * 1_000
        return {
            "fresh": points > 0,
            "points": points,
            "generation_id": snapshot.generation_id,
            "commit_sha": snapshot.commit_sha,
            "count_latency_ms": round(latency_ms, 3),
        }
    finally:
        await index.aclose()
        await embedding.aclose()
        await query.aclose()


def _json_default(value: object) -> object:
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    try:
        return asdict(value)  # type: ignore[call-overload]
    except TypeError:
        return str(value)


def main() -> None:
    """Run a restricted M3 administrative command."""
    args = _parser().parse_args()
    settings = load_settings()
    if args.command == "db":
        if args.db_command == "upgrade":
            upgrade_database(settings.database.url)
            result: object = {"revision": current_revision(settings.database.url)}
        elif args.db_command == "downgrade":
            downgrade_database(settings.database.url, args.revision)
            result = {"revision": current_revision(settings.database.url)}
        else:
            result = {"revision": current_revision(settings.database.url)}
    else:
        if args.command == "conversations":
            result = asyncio.run(_run_conversations(args, settings))
        elif args.command == "vector":
            result = asyncio.run(_run_vector(args, settings))
        else:
            result = asyncio.run(_run_index(args, settings))
    if args.json_output:
        print(json.dumps(result, default=_json_default, sort_keys=True))
    else:
        print(json.dumps(result, default=_json_default, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
