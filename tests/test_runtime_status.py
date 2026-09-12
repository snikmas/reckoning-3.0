"""Runtime status must reflect the real gateway process, never a guess."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from reckoning.runtime_status import (
    gateway_runtime_status,
    record_gateway_runtime,
    runtime_path,
)


def test_missing_record_reports_stopped(tmp_path: Path) -> None:
    status = gateway_runtime_status(tmp_path)
    assert status.running is False
    assert status.pid is None


def test_live_record_reports_running_with_pid(tmp_path: Path) -> None:
    with record_gateway_runtime(tmp_path):
        status = gateway_runtime_status(tmp_path)
        assert status.running is True
        assert status.pid is not None
    assert gateway_runtime_status(tmp_path).running is False


def test_stale_pid_is_reported_as_stopped(tmp_path: Path) -> None:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    runtime_path(tmp_path).write_text(
        json.dumps({"command": "reckoning gateway", "pid": process.pid}),
        encoding="utf-8",
    )

    assert gateway_runtime_status(tmp_path).running is False


def test_corrupt_record_is_reported_as_stopped(tmp_path: Path) -> None:
    runtime_path(tmp_path).write_text("not json", encoding="utf-8")
    assert gateway_runtime_status(tmp_path).running is False


def test_record_is_not_written_without_an_installation_root(tmp_path: Path) -> None:
    absent = tmp_path / "absent"
    with record_gateway_runtime(absent):
        assert not runtime_path(absent).exists()
