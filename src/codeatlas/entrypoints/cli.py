"""Local diagnostic CLI that reuses the same core as the Discord adapter."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import secrets
import sys
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from codeatlas.application.interactions import InteractionOutcome
from codeatlas.config.settings import CodeAtlasSettings, load_settings
from codeatlas.domain.conversation_models import ConversationId
from codeatlas.domain.errors import CodeAtlasError
from codeatlas.domain.interaction_models import (
    Actor,
    CanonicalInteraction,
    CanonicalResponse,
    ConversationRef,
    InboundMessage,
    InteractionCapabilities,
    InteractionKind,
)
from codeatlas.runtime import CodeAtlasRuntime

logger = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codeatlas-ask",
        description="Ask the configured CodeAtlas repositories a question without Discord.",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    subcommands = parser.add_subparsers(dest="command", required=True)
    ask = subcommands.add_parser("ask")
    ask.add_argument("question", nargs="+")
    ask.add_argument(
        "--repo",
        action="append",
        default=[],
        type=Path,
        dest="repositories",
        help="additional approved local repository path (repeatable)",
    )
    repos = subcommands.add_parser("repos")
    repos_commands = repos.add_subparsers(dest="repos_command", required=True)
    repos_commands.add_parser("list")
    return parser


def _apply_repository_overrides(
    settings: CodeAtlasSettings, paths: list[Path]
) -> CodeAtlasSettings:
    if not paths:
        return settings
    configured = list(settings.repository.registered_paths)
    for path in paths:
        resolved = path.expanduser()
        if resolved not in configured:
            configured.append(resolved)
    repository = settings.repository.model_copy(update={"registered_paths": tuple(configured)})
    return settings.model_copy(update={"repository": repository})


def _json_default(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return str(value)


def _response_payload(response: CanonicalResponse) -> dict[str, object]:
    return {
        "status": response.status.value,
        "confidence": response.confidence,
        "blocks": [
            {"kind": block.kind.value, "text": block.text, "language": block.language}
            for block in response.blocks
        ],
        "citations": [
            {
                "citation_id": citation.citation_id,
                "repository_id": citation.repository_id,
                "commit_sha": citation.commit_sha,
                "path": citation.path,
                "start_line": citation.start_line,
                "end_line": citation.end_line,
                "symbol_name": citation.symbol_name,
                "retrieval_method": citation.retrieval_method,
            }
            for citation in response.citations
        ],
        "uncertainties": list(response.uncertainties),
        "search_methods": list(response.search_methods),
        "follow_ups": list(response.follow_ups),
        "safe_error": response.safe_error,
    }


async def _ask(
    runtime: CodeAtlasRuntime,
    question: str,
) -> InteractionOutcome:
    request_token = secrets.token_hex(16)
    conversation_id = ConversationId.derive("cli", f"session-{request_token}")
    now = datetime.now(UTC)
    interaction = CanonicalInteraction(
        interaction_id=f"cli_msg_{request_token}",
        idempotency_key=f"cli:{request_token}",
        kind=InteractionKind.CLI,
        actor=Actor(actor_id="cli_user_1", display_name="cli"),
        conversation=ConversationRef(
            conversation_id=conversation_id.value,
            source="cli",
            is_thread=False,
        ),
        message=InboundMessage(
            message_id=f"cli_message_{request_token}",
            content=question,
            created_at=now,
        ),
        capabilities=InteractionCapabilities.plain_cli(),
        locale="ja",
        received_at=now,
    )
    return await runtime.interactions.handle(interaction)


async def _run(args: argparse.Namespace, settings: CodeAtlasSettings) -> int:
    repository_overrides: list[Path] = getattr(args, "repositories", [])
    settings = _apply_repository_overrides(settings, repository_overrides)
    runtime = CodeAtlasRuntime.create(
        settings,
        enable_discord=False,
        log_stream=sys.stderr if args.json_output else None,
    )
    try:
        await runtime.start()
        if args.command == "repos":
            payload = [
                {
                    "repository_id": descriptor.repository_id.value,
                    "name": descriptor.name,
                    "default_branch": descriptor.default_branch,
                }
                for descriptor in runtime.registered_repositories
            ]
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0

        question = " ".join(args.question).strip()
        if not question:
            print("A question is required.")
            return 2
        outcome = await _ask(runtime, question)
        response = outcome.response
        if args.json_output:
            if response is None:
                print(json.dumps({"duplicate": outcome.duplicate, "response": None}))
            else:
                print(
                    json.dumps(
                        {
                            "duplicate": outcome.duplicate,
                            "response": _response_payload(response),
                        },
                        indent=2,
                        sort_keys=True,
                        default=_json_default,
                    )
                )
        elif response is not None:
            print(response.plain_text)
        elif outcome.prior_response_text is not None:
            print(outcome.prior_response_text)
        return 0 if response is not None and response.safe_error is None else 1
    finally:
        await runtime.stop()


def main() -> None:
    """Run one diagnostic interaction against the configured repositories."""
    args = _parser().parse_args()
    try:
        exit_code = asyncio.run(_run(args, load_settings()))
    except CodeAtlasError as error:
        logger.error("cli_safe_failure", extra={"error_code": error.code})
        raise SystemExit(1) from None
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
