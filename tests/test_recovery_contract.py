from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from reckoning.automation import (
    BriefingFinding,
    BriefingPolicy,
    BriefingService,
    JsonFileAutomationRepository,
    RoutineService,
    RoutineStep,
    StepResult,
    WatchFinding,
    WatchService,
)
from reckoning.connectors import (
    ConnectorItem,
    ConnectorService,
    ExternalWriteService,
    WriteResult,
)
from reckoning.operations import create_transfer, restore_transfer, setup_instance
from reckoning.personal_context import (
    JsonFilePersonalContextRepository,
    PersonalContextService,
)


NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
PASSPHRASE = "correct-horse-battery-staple"


class ReadAdapter:
    connector_id = "calendar"
    read_scopes = ("events:read",)
    write_scopes = ("events:write",)

    def verify_identity(self) -> str:
        return "mary-calendar"

    def synchronize(self, read_scope: tuple[str, ...]) -> tuple[ConnectorItem, ...]:
        assert read_scope == self.read_scopes
        return (ConnectorItem("event-1", "Exam at 09:00", "calendar:event-1"),)


class SuccessfulRoutineStep:
    def execute(self, step: RoutineStep, idempotency_key: str) -> StepResult:
        return StepResult("success", f"{step.action}:{idempotency_key}")


class RecordingWriteAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, payload: dict[str, str], idempotency_key: str) -> WriteResult:
        self.calls += 1
        return WriteResult("success", "event-9", f"created:{idempotency_key}")


def _populate_real_operational_state(data_dir: Path) -> None:
    setup_instance(data_dir, "local")

    context_repository = JsonFilePersonalContextRepository(
        data_dir / "personal-context.json"
    )
    context = PersonalContextService(context_repository)
    context.remember(
        record_id="fact-active",
        original_text="My exam is on Monday.",
        language="en",
        canonical_meaning="Mary's exam is on Monday.",
        source="direct user statement",
        created_at=NOW,
    )
    context.correct(
        "fact-active",
        original_text="My exam is on Tuesday.",
        language="en",
        canonical_meaning="Mary's exam is on Tuesday.",
        corrected_at=NOW,
    )
    context.remember(
        record_id="fact-deleted",
        original_text="Delete this.",
        language="en",
        canonical_meaning="This record must be deleted.",
        source="direct user statement",
        created_at=NOW,
    )
    context.delete("fact-deleted", NOW)

    routine_repository = JsonFileAutomationRepository(data_dir / "routines.json")
    routines = RoutineService(routine_repository)
    proposal = routines.propose(
        proposal_id="routine-v1",
        routine_id="weekly-goal-check",
        source_request="Check my goal every Sunday.",
        created_at=NOW,
        trigger="0 18 * * SUN",
        source_scope=("official-source",),
        context_scope=("goal:exam",),
        tools=("read-official-source",),
        permissions=("read:official-source",),
        delivery="private-web",
        model_policy="deterministic only",
        cost_ceiling=1,
        retry_limit=0,
        delegation_policy="direct execution only",
        failure_behavior="record failure",
    )
    routines.confirm(proposal.id)
    routines.start_run(
        run_id="routine-run-1",
        proposal_id=proposal.id,
        scheduled_for=NOW,
        idempotency_key="weekly-goal-check:2026-08-31",
        steps=(RoutineStep("read", "deterministic", "read-official-source"),),
    )
    routines.resume_run(
        "routine-run-1",
        deterministic_executor=SuccessfulRoutineStep(),
        model_executor=None,
        completed_at=NOW,
    )

    watches = WatchService(data_dir / "watches.json")
    watch = watches.propose(
        watch_id="exam-watch",
        source_request_id="request-1",
        trigger="daily",
        source_scope=("official-source",),
        notification_threshold=0.8,
        budget=2,
        delivery_policy="private-web",
        open_ended=False,
    )
    watches.confirm(watch.id)
    watches.run(
        watch.id,
        run_id="watch-run-1",
        findings=(
            WatchFinding(
                "weak-finding",
                "official-source",
                "goal:exam",
                "An immaterial wording change.",
                credibility=0.5,
                relevance=0.5,
                cost_units=1,
                evidence="Dated source snapshot",
            ),
        ),
        checked_at=NOW,
    )

    briefings = BriefingService(data_dir / "briefings.json")
    briefings.deliver(
        run_id="briefing-run-1",
        policy=BriefingPolicy(0, 0, "private-web"),
        findings=(
            BriefingFinding(
                "briefing-suppressed",
                "No material change",
                0.1,
                0.8,
                0.1,
                0.1,
                1,
                0.8,
                "private-web",
            ),
        ),
        delivered_at=NOW,
    )

    connectors = ConnectorService(path=data_dir / "connectors.json")
    connectors.connect(ReadAdapter(), read_scope=("events:read",))
    connectors.synchronize("calendar", synchronized_at=NOW)
    connectors.remove_imported_data("calendar", mode="delete")

    writes = ExternalWriteService(data_dir / "external-writes.json")
    permission = writes.grant_standing_permission(
        permission_id="calendar-create",
        connector_id="calendar",
        action_type="create-event",
        target="calendar:mary",
        trigger="weekly-plan",
        boundary="before 18:00",
        granted_at=NOW,
    )
    writes.revoke_standing_permission(permission.id, revoked_at=NOW)
    pending = writes.prepare(
        write_id="pending-after-revoke",
        connector_id="calendar",
        action_type="create-event",
        target="calendar:mary",
        trigger="weekly-plan",
        boundary="before 18:00",
        payload={"title": "Must still require approval"},
        prepared_at=NOW,
    )
    executed = writes.prepare(
        write_id="completed-write",
        connector_id="calendar",
        action_type="create-event",
        target="calendar:mary",
        trigger="manual",
        boundary="one event",
        payload={"title": "Already created"},
        prepared_at=NOW,
    )
    writes.approve_exact(executed.id, approval_id="approval-1", approved_at=NOW)
    result = writes.execute(executed.id, RecordingWriteAdapter(), completed_at=NOW)
    assert pending.id == "pending-after-revoke"
    assert result.status == "success"


def test_clean_restore_preserves_real_visible_operational_contract(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    restored = tmp_path / "restored"
    archive = tmp_path / "state.reckoning"
    _populate_real_operational_state(source)

    create_transfer(source, archive, PASSPHRASE, kind="backup")
    restore_transfer(archive, restored, PASSPHRASE)

    restored_context_repository = JsonFilePersonalContextRepository(
        restored / "personal-context.json"
    )
    restored_context = PersonalContextService(restored_context_repository)
    active = restored_context.inspect("fact-active")
    assert active.current.record_id == "fact-active"
    assert active.current.version == 2
    assert active.current.source == "direct user correction"
    assert active.current.supersedes_version == 1
    assert tuple(
        marker.deleted_record_id
        for marker in restored_context_repository.suppression_markers()
    ) == ("fact-deleted",)
    with pytest.raises(KeyError, match="fact-deleted"):
        restored_context.inspect("fact-deleted")

    restored_routine_repository = JsonFileAutomationRepository(
        restored / "routines.json"
    )
    restored_routines = RoutineService(restored_routine_repository)
    assert restored_routines.inspect_proposal("routine-v1").version == 1
    assert restored_routine_repository.get_receipt("routine-run-1").status == "success"

    restored_watches = WatchService(restored / "watches.json")
    assert restored_watches.inspect("exam-watch").confirmed is True
    assert tuple(
        item.id for item in restored_watches.receipt("watch-run-1").suppressed
    ) == ("weak-finding",)

    restored_briefings = BriefingService(restored / "briefings.json")
    assert restored_briefings.receipt("briefing-run-1").suppressed_ids == (
        "briefing-suppressed",
    )

    restored_connectors = ConnectorService(path=restored / "connectors.json")
    restored_connectors.connect(ReadAdapter(), read_scope=("events:read",))
    restored_connectors.synchronize("calendar", synchronized_at=NOW)
    assert restored_connectors.imported_items("calendar") == ()

    restored_writes = ExternalWriteService(restored / "external-writes.json")
    pending_adapter = RecordingWriteAdapter()
    pending = restored_writes.execute(
        "pending-after-revoke", pending_adapter, completed_at=NOW
    )
    assert pending.status == "approval_required"
    assert pending_adapter.calls == 0
    completed_adapter = RecordingWriteAdapter()
    completed = restored_writes.execute(
        "completed-write", completed_adapter, completed_at=NOW
    )
    assert completed.status == "success"
    assert completed_adapter.calls == 0


def test_failed_final_restore_swap_keeps_an_existing_clean_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    archive = tmp_path / "state.reckoning"
    setup_instance(source, "local")
    create_transfer(source, archive, PASSPHRASE, kind="backup")
    target.mkdir()

    def fail_swap(source_path: Path, target_path: Path) -> None:
        raise OSError(f"cannot swap {source_path.name} into {target_path.name}")

    monkeypatch.setattr("reckoning.operations.os.replace", fail_swap)

    with pytest.raises(OSError, match="cannot swap"):
        restore_transfer(archive, target, PASSPHRASE)

    assert target.is_dir()
    assert tuple(target.iterdir()) == ()
    assert tuple(tmp_path.glob(".target.restore-*")) == ()
