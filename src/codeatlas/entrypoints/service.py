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


def main() -> None:
    """Run the asynchronous CodeAtlas service lifecycle."""
    try:
        asyncio.run(serve())
    except Exception as error:
        logger.error(
            "service_failed",
            extra={"error_type": type(error).__name__},
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
