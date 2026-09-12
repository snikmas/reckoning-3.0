"""Truthful runtime status for the foreground gateway process.

The gateway is a foreground process, so it leaves a small pid record in the
installation data directory while it runs and removes that record when it
exits. Setup reads the record instead of assuming the gateway is stopped.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

GATEWAY_COMMAND = "reckoning gateway"
RUNTIME_FILE_NAME = "gateway-runtime.json"


@dataclass(frozen=True)
class GatewayRuntime:
    running: bool
    pid: int | None = None


def runtime_path(data_dir: Path) -> Path:
    return data_dir.expanduser() / RUNTIME_FILE_NAME


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def gateway_runtime_status(data_dir: Path) -> GatewayRuntime:
    """Report the recorded gateway process, never guessing from absence."""
    try:
        payload = json.loads(runtime_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return GatewayRuntime(False)
    raw_pid = payload.get("pid") if isinstance(payload, dict) else None
    if not isinstance(raw_pid, int) or not _process_alive(raw_pid):
        return GatewayRuntime(False)
    return GatewayRuntime(True, raw_pid)


@contextmanager
def record_gateway_runtime(data_dir: Path) -> Iterator[None]:
    """Record this process as the running gateway for the duration of the block.

    The installation data directory must already exist; setup creates it. A
    missing directory means no installation can run, so no record is written.
    """
    path = runtime_path(data_dir)
    if not path.parent.is_dir():
        yield
        return
    try:
        path.write_text(
            json.dumps({"command": GATEWAY_COMMAND, "pid": os.getpid()}),
            encoding="utf-8",
        )
    except OSError:
        yield
        return
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
