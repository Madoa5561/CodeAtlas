"""Lifecycle owner for the local FastAPI server."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import uvicorn
from fastapi import FastAPI

from codeatlas import API_VERSION
from codeatlas.config.settings import ApiSettings
from codeatlas.plugin_api import PluginCapability, PluginManifest, PluginOrigin

logger = logging.getLogger(__name__)

HTTP_API_CAPABILITY = "interaction:http-api"


@contextmanager
def _no_signal_capture() -> Iterator[None]:
    """Keep the application's signal handlers instead of uvicorn's."""
    yield


@dataclass(slots=True)
class ApiServerPlugin:
    """Start and stop the ASGI server inside the application event loop."""

    app: FastAPI
    settings: ApiSettings
    started: bool = False
    _server: uvicorn.Server | None = field(default=None, repr=False)
    _task: asyncio.Task[None] | None = field(default=None, repr=False)
    manifest = PluginManifest(
        plugin_id="codeatlas.interaction.http-api",
        version="1.0.0",
        api_version_min=API_VERSION,
        api_version_max=API_VERSION,
        capabilities=(PluginCapability("interaction", "http-api", "1.0.0"),),
        origin=PluginOrigin.BUILT_IN,
    )

    @classmethod
    def from_settings(cls, app: FastAPI, settings: ApiSettings) -> ApiServerPlugin:
        """Build the server plugin from the prepared ASGI app."""
        return cls(app=app, settings=settings)

    async def start(self) -> None:
        """Start uvicorn in a background task without hijacking signal handlers."""
        if self.started:
            return
        config = uvicorn.Config(
            self.app,
            host=self.settings.host,
            port=self.settings.port,
            log_config=None,
            access_log=False,
            lifespan="off",
            server_header=False,
            date_header=False,
        )
        server = uvicorn.Server(config)
        # The application entrypoint owns SIGINT/SIGTERM handling. Uvicorn must not
        # replace those handlers, so disable its signal capture for the embedded server.
        server.capture_signals = _no_signal_capture  # type: ignore[method-assign]
        self._server = server
        self._task = asyncio.create_task(server.serve(), name="codeatlas-api")
        for _ in range(100):
            if server.started:
                break
            if self._task.done():
                self._task.result()
                raise RuntimeError("The API server stopped before becoming ready.")
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("The API server did not become ready in time.")
        self.started = True
        logger.info(
            "api_server_started",
            extra={"host": self.settings.host, "port": self.settings.port},
        )

    async def stop(self) -> None:
        """Ask uvicorn to exit and await the background task."""
        if not self.started and self._server is None:
            return
        self.started = False
        server, task = self._server, self._task
        self._server = None
        self._task = None
        if server is not None:
            server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=10.0)
            except TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("api_server_stopped")
