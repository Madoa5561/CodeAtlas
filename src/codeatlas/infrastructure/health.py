"""Process readiness state shared with the container healthcheck."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

HEALTH_STATE_PATH = Path(tempfile.gettempdir()) / "codeatlas-ready"


def mark_process_ready(path: Path = HEALTH_STATE_PATH) -> None:
    """Publish the current process identifier after application startup."""
    path.write_text(str(os.getpid()), encoding="ascii")


def clear_process_ready(path: Path = HEALTH_STATE_PATH) -> None:
    """Remove readiness state before application shutdown."""
    path.unlink(missing_ok=True)


def is_process_ready(path: Path = HEALTH_STATE_PATH) -> bool:
    """Return whether the published service process still exists."""
    try:
        process_id = int(path.read_text(encoding="ascii"))
        if process_id <= 0:
            return False
        os.kill(process_id, 0)
    except (OSError, ValueError):
        return False
    return True
