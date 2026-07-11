"""Long-running M1 process with graceful signal-driven shutdown."""

from __future__ import annotations

import asyncio
import signal

from codeatlas.bootstrap import create_application


async def serve() -> None:
    """Run the foundation process until an operating-system stop signal arrives."""
    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop_requested.set)
        except NotImplementedError:
            signal.signal(
                signal_name,
                lambda _number, _frame: loop.call_soon_threadsafe(stop_requested.set),
            )

    application = create_application()
    async with application:
        await stop_requested.wait()


def main() -> None:
    """Run the asynchronous CodeAtlas service lifecycle."""
    asyncio.run(serve())


if __name__ == "__main__":
    main()
