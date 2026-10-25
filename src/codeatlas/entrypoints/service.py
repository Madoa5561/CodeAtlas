"""Long-running CodeAtlas process with graceful signal-driven shutdown."""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path
from typing import Protocol

from codeatlas.config.settings import CodeAtlasSettings, load_settings
from codeatlas.infrastructure.health import (
    HEALTH_STATE_PATH,
    clear_process_ready,
    mark_process_ready,
)
from codeatlas.runtime import CodeAtlasRuntime

logger = logging.getLogger(__name__)


class ManagedApplication(Protocol):
    """Structural lifecycle contract shared by tests and the real runtime."""

    @property
    def settings(self) -> CodeAtlasSettings: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


async def stop_application(application: ManagedApplication, timeout_seconds: float) -> None:
    """Stop the application within its configured graceful-shutdown deadline."""
    async with asyncio.timeout(timeout_seconds):
        await application.stop()


async def run_service(
    application: ManagedApplication,
    stop_requested: asyncio.Event,
    *,
    health_path: Path = HEALTH_STATE_PATH,
) -> None:
    """Run an application until shutdown is requested, publishing readiness meanwhile."""
    await application.start()
    try:
        mark_process_ready(health_path)
        await stop_requested.wait()
    finally:
        clear_process_ready(health_path)
        await stop_application(
            application,
            application.settings.application.shutdown_timeout_seconds,
        )


def install_signal_handlers(
    stop_requested: asyncio.Event,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Install native async handlers or the Windows-compatible signal fallback."""
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop_requested.set)
        except NotImplementedError:
            signal.signal(
                signal_name,
                lambda _number, _frame: loop.call_soon_threadsafe(stop_requested.set),
            )


async def serve() -> None:
    """Run the M4/M5 runtime until an operating-system stop signal arrives."""
    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    install_signal_handlers(stop_requested, loop)
    runtime = CodeAtlasRuntime.create(load_settings())
    await run_service(runtime, stop_requested)


def _safe_error_details(error: BaseException) -> str:
    """Return a value-free startup failure description safe to log.

    Pydantic validation errors are reduced to field locations only. Input values
    are never logged, so a configuration typo cannot leak a secret.
    """
    candidate: object = getattr(error, "internal_cause", None) or error
    errors_attr: object = getattr(candidate, "errors", None)
    if callable(errors_attr):
        try:
            items: object = errors_attr()
        except Exception:
            items = []
        fields: list[str] = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                loc = item.get("loc")
                if isinstance(loc, list | tuple) and loc:
                    fields.append(".".join(str(part) for part in loc))
        if fields:
            return "fields=" + ",".join(sorted(set(fields))[:10])
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return f"error_code={code}"
    return f"error_type={type(error).__name__}"


def main() -> None:
    """Run the asynchronous CodeAtlas service lifecycle."""
    try:
        asyncio.run(serve())
    except Exception as error:
        logger.error("service_failed %s", _safe_error_details(error))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
