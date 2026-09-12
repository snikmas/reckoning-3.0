from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import threading

import pytest

from reckoning.automation import (
    JsonFileAutomationRepository,
    RoutineProposal,
    RoutineRunClaimConflict,
    RoutineService,
    RoutineStep,
    StepResult,
    _proposal_to_data,
    _receipt_to_data,
    _run_to_data,
)
from reckoning.operations import create_transfer, restore_transfer, setup_instance
from reckoning.root_database import ROOT_DATABASE_FILENAME


NOW = datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc)
PASSPHRASE = "correct-horse-battery-staple"


def _propose(
    service: RoutineService,
    proposal_id: str,
    routine_id: str,
    *,
    retry_limit: int = 0,
) -> None:
    service.propose(
        proposal_id=proposal_id,
        routine_id=routine_id,
        source_request=f"Run synthetic routine {routine_id}.",
        created_at=NOW,
        trigger="manual synthetic trigger",
        source_scope=(f"source:{routine_id}",),
        context_scope=(f"context:{routine_id}",),
        tools=("read-source",),
        permissions=(f"permission:{routine_id}",),
        delivery="private-web",
        model_policy="no model",
        cost_ceiling=0,
        retry_limit=retry_limit,
        delegation_policy="direct execution only",
        failure_behavior="record failure",
    )


def _confirmed(service: RoutineService, proposal_id: str, routine_id: str) -> str:
    _propose(service, proposal_id, routine_id)
    service.confirm(proposal_id)
    return proposal_id


class RecordingStepExecutor:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def execute(self, step: RoutineStep, idempotency_key: str) -> StepResult:
        self.keys.append(idempotency_key)
        return StepResult("success", f"{step.action}:{idempotency_key}")


def _steps(count: int) -> tuple[RoutineStep, ...]:
    return tuple(
        RoutineStep(f"step-{index}", "deterministic", "read-source")
        for index in range(count)
    )


def test_two_stale_services_confirm_independent_proposals(tmp_path: Path) -> None:
    path = tmp_path / "automation.json"
    seeded = RoutineService(JsonFileAutomationRepository(path))
    _propose(seeded, "proposal-a", "routine-a")
    _propose(seeded, "proposal-b", "routine-b")
    first = RoutineService(JsonFileAutomationRepository(path))
    second = RoutineService(JsonFileAutomationRepository(path))

    first.confirm("proposal-a")
    second.confirm("proposal-b")

    reopened = RoutineService(JsonFileAutomationRepository(path))
    statuses = {item.id: item.status for item in reopened._repository.list_proposals()}
    assert statuses == {"proposal-a": "confirmed", "proposal-b": "confirmed"}


def test_confirmation_and_supersession_race_for_one_routine(tmp_path: Path) -> None:
    path = tmp_path / "automation.json"
    service = RoutineService(JsonFileAutomationRepository(path))
    _confirmed(service, "routine-v1", "weekly-review")
    revision = service.revise(
        "routine-v1",
        proposal_id="routine-v2",
        created_at=NOW,
        changes={"source_scope": ("source:weekly-review", "source:extra")},
    )

    service.confirm(revision.id)

    statuses = {
        item.id: item.status
        for item in JsonFileAutomationRepository(path).list_proposals()
    }
    assert statuses == {"routine-v1": "superseded", "routine-v2": "confirmed"}
    with pytest.raises(ValueError, match="superseded"):
        service.confirm("routine-v1")


def test_two_workers_cannot_run_the_same_active_claim(tmp_path: Path) -> None:
    path = tmp_path / "automation.json"
    service = RoutineService(JsonFileAutomationRepository(path))
    proposal_id = _confirmed(service, "routine-v1", "weekly-review")
    service.start_run(
        run_id="run-1",
        proposal_id=proposal_id,
        scheduled_for=NOW,
        idempotency_key="weekly-review:2026-09-11",
        steps=_steps(1),
        triggered_by="manual synthetic trigger",
    )
    repository = JsonFileAutomationRepository(path)
    repository.claim_run("run-1", owner="worker-a", claimed_at=NOW)
    executor = RecordingStepExecutor()

    with pytest.raises(RoutineRunClaimConflict):
        service.resume_run(
            "run-1",
            deterministic_executor=executor,
            model_executor=None,
            completed_at=NOW,
            worker="worker-b",
        )

    assert executor.keys == []


def test_explicit_recovery_records_the_previous_claim_owner(tmp_path: Path) -> None:
    path = tmp_path / "automation.json"
    service = RoutineService(JsonFileAutomationRepository(path))
    proposal_id = _confirmed(service, "routine-v1", "weekly-review")
    service.start_run(
        run_id="run-1",
        proposal_id=proposal_id,
        scheduled_for=NOW,
        idempotency_key="weekly-review:2026-09-11",
        steps=_steps(1),
        triggered_by="manual synthetic trigger",
    )
    repository = JsonFileAutomationRepository(path)
    revision = repository.claim_run("run-1", owner="crashed-worker", claimed_at=NOW)

    recovered = repository.recover_run(
        "run-1",
        expected_revision=revision,
        new_owner="recovery-worker",
        reason="operator recovered an interrupted synthetic run",
        recovered_at=NOW,
    )

    assert recovered == revision + 1
    assert repository.get_run("run-1").id == "run-1"
    with pytest.raises(RoutineRunClaimConflict):
        repository.recover_run(
            "run-1",
            expected_revision=revision,
            new_owner="stale-worker",
            reason="stale recovery",
            recovered_at=NOW,
        )


def test_restart_after_a_step_boundary_resumes_without_repeating_steps(
    tmp_path: Path,
) -> None:
    path = tmp_path / "automation.json"
    service = RoutineService(JsonFileAutomationRepository(path))
    _propose(service, "routine-v1", "weekly-review", retry_limit=1)
    service.confirm("routine-v1")
    service.start_run(
        run_id="run-1",
        proposal_id="routine-v1",
        scheduled_for=NOW,
        idempotency_key="weekly-review:2026-09-11",
        steps=_steps(3),
        triggered_by="manual synthetic trigger",
    )
    first_calls: list[str] = []

    class InterruptingExecutor:
        def execute(self, step: RoutineStep, idempotency_key: str) -> StepResult:
            first_calls.append(idempotency_key)
            if step.id == "step-1":
                raise InterruptedError("synthetic crash at a step boundary")
            return StepResult("success", f"{step.id}:{idempotency_key}")

    with pytest.raises(InterruptedError):
        service.resume_run(
            "run-1",
            deterministic_executor=InterruptingExecutor(),
            model_executor=None,
            completed_at=NOW,
        )

    restarted = RoutineService(JsonFileAutomationRepository(path))
    executor = RecordingStepExecutor()
    receipt = restarted.resume_run(
        "run-1",
        deterministic_executor=executor,
        model_executor=None,
        completed_at=NOW,
    )

    assert receipt.status == "success"
    assert not any(key.endswith(":step-0") for key in executor.keys)
    assert any(key.endswith(":step-1") for key in executor.keys)
    assert any(key.endswith(":step-2") for key in executor.keys)
    terminal = restarted.inspect_run("run-1")
    assert terminal.status == receipt.status
    assert JsonFileAutomationRepository(path).get_receipt("run-1") == receipt


def test_restart_returns_the_existing_receipt(tmp_path: Path) -> None:
    path = tmp_path / "automation.json"
    service = RoutineService(JsonFileAutomationRepository(path))
    proposal_id = _confirmed(service, "routine-v1", "weekly-review")
    service.start_run(
        run_id="run-1",
        proposal_id=proposal_id,
        scheduled_for=NOW,
        idempotency_key="weekly-review:2026-09-11",
        steps=_steps(1),
        triggered_by="manual synthetic trigger",
    )
    executor = RecordingStepExecutor()
    receipt = service.resume_run(
        "run-1",
        deterministic_executor=executor,
        model_executor=None,
        completed_at=NOW,
    )

    restarted_executor = RecordingStepExecutor()
    repeated = RoutineService(JsonFileAutomationRepository(path)).resume_run(
        "run-1",
        deterministic_executor=restarted_executor,
        model_executor=None,
        completed_at=NOW,
    )

    assert repeated == receipt
    assert len(executor.keys) == 1
    assert restarted_executor.keys == []


def test_start_run_is_idempotent_by_key(tmp_path: Path) -> None:
    path = tmp_path / "automation.json"
    service = RoutineService(JsonFileAutomationRepository(path))
    proposal_id = _confirmed(service, "routine-v1", "weekly-review")
    first = service.start_run(
        run_id="run-1",
        proposal_id=proposal_id,
        scheduled_for=NOW,
        idempotency_key="weekly-review:2026-09-11",
        steps=_steps(1),
        triggered_by="manual synthetic trigger",
    )
    second = service.start_run(
        run_id="run-2",
        proposal_id=proposal_id,
        scheduled_for=NOW,
        idempotency_key="weekly-review:2026-09-11",
        steps=_steps(1),
        triggered_by="manual synthetic trigger",
    )

    assert second.id == first.id == "run-1"


def test_legacy_automation_migrates_once_with_rollback(tmp_path: Path) -> None:
    path = tmp_path / "automation.json"
    proposal = RoutineService(
        JsonFileAutomationRepository(tmp_path / "seed" / "automation.json")
    )
    _propose(proposal, "routine-v1", "weekly-review")
    confirmed = proposal.confirm("routine-v1")
    run = proposal.start_run(
        run_id="run-1",
        proposal_id=confirmed.id,
        scheduled_for=NOW,
        idempotency_key="weekly-review:2026-09-11",
        steps=_steps(1),
        triggered_by="manual synthetic trigger",
    )
    receipt = proposal.resume_run(
        "run-1",
        deterministic_executor=RecordingStepExecutor(),
        model_executor=None,
        completed_at=NOW,
    )
    legacy = {
        "schema_version": 1,
        "proposals": [_proposal_to_data(confirmed)],
        "runs": [_run_to_data(run)],
        "receipts": [_receipt_to_data(receipt)],
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")

    migrated = RoutineService(JsonFileAutomationRepository(path))
    assert migrated.inspect_proposal("routine-v1").status == "confirmed"
    assert migrated.inspect_run("run-1").id == "run-1"
    assert migrated._repository.get_receipt("run-1") == receipt
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": 2,
        "authority": "sqlite",
        "database": ROOT_DATABASE_FILENAME,
        "migration_state": "sqlite-authoritative",
    }
    rollback = path.with_name("automation.rollback.json")
    assert json.loads(rollback.read_text(encoding="utf-8")) == json.loads(
        json.dumps(legacy)
    )


def test_invalid_legacy_automation_does_not_activate_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "automation.json"
    duplicate = _proposal_to_data(
        RoutineProposal(
            id="duplicate",
            routine_id="weekly-review",
            version=1,
            source_request="Synthetic.",
            created_at=NOW,
            trigger="manual",
            source_scope=("source",),
            context_scope=("context",),
            tools=("read-source",),
            permissions=("permission",),
            delivery="private-web",
            model_policy="no model",
            cost_ceiling=0,
            retry_limit=0,
            delegation_policy="direct execution only",
            failure_behavior="record failure",
        )
    )
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "proposals": [duplicate, duplicate],
                "runs": [],
                "receipts": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Invalid legacy automation"):
        JsonFileAutomationRepository(path)


def test_interrupted_migration_recovers_from_the_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import reckoning.automation_store as store

    path = tmp_path / "automation.json"
    proposal = RoutineService(JsonFileAutomationRepository(tmp_path / "seed" / "automation.json"))
    _propose(proposal, "routine-v1", "weekly-review")
    confirmed = proposal.confirm("routine-v1")
    legacy = {
        "schema_version": 1,
        "proposals": [_proposal_to_data(confirmed)],
        "runs": [],
        "receipts": [],
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")
    original = store._import_legacy
    calls = {"count": 0}

    def flaky(connection, legacy_state):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("interrupted migration")
        return original(connection, legacy_state)

    monkeypatch.setattr(store, "_import_legacy", flaky)
    with pytest.raises(RuntimeError, match="interrupted migration"):
        JsonFileAutomationRepository(path)

    repository = JsonFileAutomationRepository(path)
    assert repository.get_proposal("routine-v1").status == "confirmed"


def test_concurrent_first_initialization_is_safe(tmp_path: Path) -> None:
    path = tmp_path / "automation.json"
    barrier = threading.Barrier(6)

    def initialize() -> None:
        barrier.wait()
        JsonFileAutomationRepository(path)

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(lambda _index: initialize(), range(6)))

    service = RoutineService(JsonFileAutomationRepository(path))
    _propose(service, "routine-v1", "weekly-review")
    assert service.inspect_proposal("routine-v1").status == "proposed"


def test_clean_encrypted_restore_preserves_routine_authority(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    restored = tmp_path / "restored"
    archive = tmp_path / "state.reckoning"
    setup_instance(source, "local")
    service = RoutineService(
        JsonFileAutomationRepository(source / "confirmed-state" / "automation.json")
    )
    proposal_id = _confirmed(service, "routine-v1", "weekly-review")
    service.start_run(
        run_id="run-1",
        proposal_id=proposal_id,
        scheduled_for=NOW,
        idempotency_key="weekly-review:2026-09-11",
        steps=_steps(1),
        triggered_by="manual synthetic trigger",
    )
    receipt = service.resume_run(
        "run-1",
        deterministic_executor=RecordingStepExecutor(),
        model_executor=None,
        completed_at=NOW,
    )

    create_transfer(source, archive, PASSPHRASE, kind="backup")
    restore_transfer(archive, restored, PASSPHRASE)

    restored_repository = JsonFileAutomationRepository(
        restored / "confirmed-state" / "automation.json"
    )
    restored_service = RoutineService(restored_repository)
    assert restored_service.inspect_proposal("routine-v1").status == "confirmed"
    assert restored_repository.get_receipt("run-1") == receipt
    executor = RecordingStepExecutor()
    repeated = restored_service.resume_run(
        "run-1",
        deterministic_executor=executor,
        model_executor=None,
        completed_at=NOW,
    )
    assert repeated == receipt
    assert executor.keys == []


def test_validate_database_accepts_automation_tables(tmp_path: Path) -> None:
    from reckoning.model_run_store import validate_database

    service = RoutineService(
        JsonFileAutomationRepository(tmp_path / "automation.json")
    )
    _propose(service, "routine-v1", "weekly-review")
    validate_database(tmp_path / ROOT_DATABASE_FILENAME)
