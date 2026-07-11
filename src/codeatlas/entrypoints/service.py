"""Long-running M1 process with graceful signal-driven shutdown."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

from codeatlas.application.container import ApplicationContainer
from codeatlas.bootstrap import create_application
from codeatlas.infrastructure.health import (
    HEALTH_STATE_PATH,
    clear_process_ready,
    mark_process_ready,
)


async def stop_application(application: ApplicationContainer, timeout_seconds: float) -> None:
    """Stop the application within its configured graceful-shutdown deadline."""
    async with asyncio.timeout(timeout_seconds):
        await application.stop()


async def run_service(
    application: ApplicationContainer,
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
    """Run the foundation process until an operating-system stop signal arrives."""
    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    install_signal_handlers(stop_requested, loop)
    await run_service(create_application(), stop_requested)


def main() -> None:
    """Run the asynchronous CodeAtlas service lifecycle."""
    asyncio.run(serve())


if __name__ == "__main__":
    main()
