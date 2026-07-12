"""Restricted M3 administrative CLI tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from codeatlas.entrypoints.index_cli import main
from codeatlas.infrastructure.indexing.store import SCHEMA_REVISION


@pytest.mark.parametrize("command", ["upgrade", "current"])
def test_database_cli_json(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Migration commands return bounded JSON without exposing source or repository paths."""
    url = f"sqlite:///{(tmp_path / 'cli.db').as_posix()}"
    monkeypatch.setenv("CODEATLAS_DATABASE__URL", url)
    if command == "current":
        monkeypatch.setattr(sys, "argv", ["codeatlas-admin", "--json", "db", "upgrade"])
        main()
        capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["codeatlas-admin", "--json", "db", command])
    main()
    output = capsys.readouterr().out
    assert json.loads(output) == {"revision": SCHEMA_REVISION}
    assert str(tmp_path) not in output
