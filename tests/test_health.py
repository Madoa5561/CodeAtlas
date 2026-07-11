"""Process readiness and shutdown deadline tests."""

import asyncio
import signal
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

import pytest

from codeatlas.bootstrap import create_application
from codeatlas.config.settings import CodeAtlasSettings
from codeatlas.entrypoints import healthcheck
from codeatlas.entrypoints.service import (
    install_signal_handlers,
    run_service,
    stop_application,
)
from codeatlas.infrastructure.health import (
    clear_process_ready,
    is_process_ready,
    mark_process_ready,
)
from codeatlas.plugin_api import PluginManifest, PluginOrigin


def test_health_state_tracks_current_process(tmp_path: Path) -> None:
    """Readiness is true only while a live process identifier is published."""
    health_path = tmp_path / "ready"
    assert is_process_ready(health_path) is False
    mark_process_ready(health_path)
    assert is_process_ready(health_path) is True
    clear_process_ready(health_path)
    assert is_process_ready(health_path) is False


def test_health_state_rejects_invalid_content(tmp_path: Path) -> None:
    """Malformed or non-positive process identifiers never report ready."""
    health_path = tmp_path / "ready"
    health_path.write_text("not-a-pid", encoding="ascii")
    assert is_process_ready(health_path) is False
    health_path.write_text("-1", encoding="ascii")
    assert is_process_ready(health_path) is False


@dataclass
class HangingStopPlugin:
    """Plugin fixture whose stop never finishes unless cancelled."""

    manifest = PluginManifest(
        "example.hanging",
        "1.0.0",
        "0.1.0",
        "0.1.0",
        origin=PluginOrigin.BUILT_IN,
    )

    async def start(self) -> None:
        """Start without external resources."""

    async def stop(self) -> None:
        """Wait forever to exercise the shutdown deadline."""
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_shutdown_timeout_cancels_hanging_plugin() -> None:
    """Graceful shutdown cannot hang beyond the configured deadline."""
    application = create_application(
        settings=CodeAtlasSettings(_env_file=None),
        plugins=(HangingStopPlugin(),),
    )
    await application.start()

    with pytest.raises(TimeoutError):
        await stop_application(application, 0.01)

    assert application.started is False


@pytest.mark.asyncio
async def test_run_service_publishes_and_clears_readiness(tmp_path: Path) -> None:
    """Service core starts, observes a stop request, clears health, and stops."""
    health_path = tmp_path / "ready"
    stop_requested = asyncio.Event()
    stop_requested.set()
    application = create_application(settings=CodeAtlasSettings(_env_file=None))

    await run_service(application, stop_requested, health_path=health_path)

    assert health_path.exists() is False
    assert application.started is False


def test_healthcheck_main_reports_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Healthcheck exits normally for a live published service process."""
    monkeypatch.setattr(healthcheck, "is_process_ready", lambda: True)
    healthcheck.main()


def test_healthcheck_main_fails_when_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Healthcheck exits unsuccessfully when no live process is published."""
    monkeypatch.setattr(healthcheck, "is_process_ready", lambda: False)
    with pytest.raises(SystemExit) as captured:
        healthcheck.main()
    assert captured.value.code == 1


def test_install_signal_handlers_uses_async_loop_callbacks() -> None:
    """Platforms with native loop signal support bind both shutdown signals."""
    stop_requested = asyncio.Event()
    loop = Mock(spec=asyncio.AbstractEventLoop)

    install_signal_handlers(stop_requested, loop)

    assert loop.add_signal_handler.call_count == 2
    callback = loop.add_signal_handler.call_args_list[0].args[1]
    callback()
    assert stop_requested.is_set()


def test_install_signal_handlers_uses_windows_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported loop handlers fall back to thread-safe signal callbacks."""
    stop_requested = asyncio.Event()
    loop = Mock(spec=asyncio.AbstractEventLoop)
    loop.add_signal_handler.side_effect = NotImplementedError
    handlers: list[Callable[[int, object], None]] = []

    def capture_handler(
        _signal_name: signal.Signals,
        handler: Callable[[int, object], None],
    ) -> None:
        handlers.append(handler)

    monkeypatch.setattr(signal, "signal", capture_handler)
    install_signal_handlers(stop_requested, loop)
    handlers[0](0, None)

    loop.call_soon_threadsafe.assert_called_once_with(stop_requested.set)
