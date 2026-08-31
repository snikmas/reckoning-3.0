from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from reckoning.automation import JsonFileAutomationRepository, RoutineService
from reckoning.connectors import ExternalWriteService


class LocalDurableConfirmationHandler:
    """Dispatch channel confirmations to the durable service that owns them."""

    def __init__(
        self,
        data_dir: Path,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._now = now or (lambda: datetime.now(timezone.utc))

    def pending_confirmation_ids(self) -> tuple[str, ...]:
        routines = RoutineService(
            JsonFileAutomationRepository(self._data_dir / "automation.json")
        )
        writes = ExternalWriteService(self._data_dir / "external-writes.json")
        return (
            *(f"routine:{item}" for item in routines.pending_confirmation_ids()),
            *(f"write:{item}" for item in writes.pending_approval_ids()),
        )

    def confirm(self, confirmation_id: str) -> bool:
        kind, separator, record_id = confirmation_id.partition(":")
        if not separator or not record_id:
            return False
        if kind == "routine":
            routines = RoutineService(
                JsonFileAutomationRepository(self._data_dir / "automation.json")
            )
            if record_id not in routines.pending_confirmation_ids():
                return False
            routines.confirm(record_id)
            return True
        if kind == "write":
            writes = ExternalWriteService(self._data_dir / "external-writes.json")
            if record_id not in writes.pending_approval_ids():
                return False
            writes.approve_exact(
                record_id,
                approval_id=confirmation_id,
                approved_at=self._now(),
            )
            return True
        return False
