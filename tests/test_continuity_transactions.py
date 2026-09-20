from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from reckoning.application import (
    ApplicationDependencies,
    InMemoryConversationStorage,
    NoConnectors,
    PlacementState,
    ReckoningApplication,
)
from reckoning.continuity import (
    CheckIn,
    Evidence,
    Inference,
    InMemoryReckoningRepository,
    OperationRecord,
    PersonalRecordProposal,
    Reckoning,
    ReckoningDraft,
    ReckoningOperationConflict,
    ReckoningRevisionConflict,
    SourcedFact,
)
from reckoning.operations import create_transfer, restore_transfer
from reckoning.persistence import JsonFileReckoningRepository
from reckoning.root_database import connect_database


NOW = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)
PASSPHRASE = "correct-horse-battery-staple"


class FixedClock:
    def __init__(self, minute: int = 0) -> None:
        self._instant = NOW.replace(minute=minute)

    def now(self) -> datetime:
        return self._instant


class DecisionProvider:
    def reckon(self, unstructured_input: str) -> ReckoningDraft:
        evidence = Evidence(
            f"evidence:{unstructured_input}", "current user message", unstructured_input
        )
        return ReckoningDraft(
            conflict=f"Conflict for {unstructured_input}",
            questions=(),
            matters_now=("Protect the fixed commitment.",),
            maintained=("Keep one small project block.",),
            parked=(),
            uncertainties=("Outcome not known yet.",),
            known=(SourcedFact(unstructured_input, (evidence.id,)),),
            inferences=(
                Inference(
                    "Capacity is shared.",
                    (evidence.id,),
                    "This remains uncertain.",
                ),
            ),
            evidence=(evidence,),
            next_step="Choose the next block.",
            proposed_records=(
                PersonalRecordProposal(
                    "decision", f"Decision for {unstructured_input}", (evidence.id,)
                ),
            ),
        )


class UnusedConversationModel:
    def respond(self, request: object) -> str:
        raise AssertionError("The continuity boundary should use the reckoning provider.")


class SequenceIdentifiers:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.next_value = 0

    def new(self) -> str:
        self.next_value += 1
        return f"{self.prefix}-{self.next_value}"


def build_application(
    state_path: Path, prefix: str, *, minute: int = 0
) -> ReckoningApplication:
    return ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(minute),
            model=UnusedConversationModel(),
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            reckoning_provider=DecisionProvider(),
            reckoning_repository=JsonFileReckoningRepository(state_path),
            identifiers=SequenceIdentifiers(prefix),
        )
    )


def test_two_instances_preserve_decisions_and_reject_stale_mutations(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "confirmed-state" / "continuity.json"
    first = build_application(state_path, "first")
    second = build_application(state_path, "second", minute=1)

    first_decision = first.start_reckoning("exam")
    second_decision = second.start_reckoning("project")
    expected_revision = first_decision.version
    corrected = second.correct_personal_record(
        first_decision.id,
        first_decision.current_records[0].record_id,
        "Protect the exam and keep one project block.",
        expected_revision=expected_revision,
    )

    with pytest.raises(ReckoningRevisionConflict, match="changed from revision 1 to 2"):
        first.correct_personal_record(
            first_decision.id,
            first_decision.current_records[0].record_id,
            "Stale correction that must not win.",
            expected_revision=expected_revision,
        )
    with pytest.raises(ReckoningRevisionConflict, match="changed from revision 1 to 2"):
        first.confirm_reckoning(
            first_decision.id, expected_revision=expected_revision
        )

    confirmed = first.confirm_reckoning(
        first_decision.id, expected_revision=corrected.version
    )
    first_check_in = first.record_check_in(
        first_decision.id, "The exam block happened."
    )
    second_check_in = second.record_check_in(
        first_decision.id, "The project block happened."
    )

    restarted = build_application(state_path, "restart", minute=2)
    resumed = restarted.resume_decision(first_decision.id)
    other = restarted.inspect_reckoning(second_decision.id)
    why = restarted.explain_reckoning(first_decision.id)

    assert confirmed.version == 3
    assert other.source_input == "project"
    assert [item.outcome for item in resumed.check_ins] == [
        "The exam block happened.",
        "The project block happened.",
    ]
    assert resumed.check_ins == (first_check_in, second_check_in)
    assert why.record_versions[0].meaning == (
        "Protect the exam and keep one project block."
    )
    assert why.record_versions[0].version == 3
    assert {item.source for item in why.evidence} == {
        "current user message",
        "direct user correction",
    }


def _operation(
    operation_id: str,
    *,
    status: str = "completed",
    result_id: str = "decision-1",
    pending_input: str = "",
    digest: str = "digest-a",
) -> OperationRecord:
    return OperationRecord(
        operation_id=operation_id,
        payload_digest=digest,
        status=status,  # type: ignore[arg-type]
        result_id=result_id,
        pending_input=pending_input,
        occurred_at=NOW,
    )


def _proposal(application: ReckoningApplication, text: str) -> Reckoning:
    return application.start_reckoning(text)


@pytest.mark.parametrize(
    "repository_factory",
    (
        lambda path: InMemoryReckoningRepository(),
        lambda path: JsonFileReckoningRepository(path / "continuity.json"),
    ),
    ids=("memory", "sqlite"),
)
def test_repository_keeps_operation_identity_with_the_saved_decision(
    tmp_path: Path, repository_factory: object
) -> None:
    repository = repository_factory(tmp_path)  # type: ignore[operator]
    application = build_application(tmp_path / "seed" / "continuity.json", "seed")
    reckoning = _proposal(application, "atomic save")

    operation = _operation("op-1", result_id=reckoning.id)
    repository.save(reckoning, expected_version=0, operation=operation)

    assert repository.lookup_operation("op-1") == operation
    assert repository.lookup_operation("missing") is None
    assert repository.list_reckonings() == (reckoning,)
    with pytest.raises(ReckoningOperationConflict):
        repository.record_operation(_operation("op-1", digest="digest-b"))


@pytest.mark.parametrize(
    "repository_factory",
    (
        lambda path: InMemoryReckoningRepository(),
        lambda path: JsonFileReckoningRepository(path / "continuity.json"),
    ),
    ids=("memory", "sqlite"),
)
def test_failed_operation_survives_until_the_retry_completes(
    tmp_path: Path, repository_factory: object
) -> None:
    repository = repository_factory(tmp_path)  # type: ignore[operator]
    failed = _operation(
        "op-2", status="failed", result_id="", pending_input="Competing commitments."
    )
    repository.record_operation(failed)

    assert repository.lookup_operation("op-2") == failed
    assert repository.list_pending_operations() == (failed,)

    application = build_application(tmp_path / "seed" / "continuity.json", "seed")
    reckoning = _proposal(application, "Competing commitments.")
    completed = _operation("op-2", result_id=reckoning.id)
    repository.save(reckoning, expected_version=0, operation=completed)

    assert repository.lookup_operation("op-2") == completed
    assert repository.list_pending_operations() == ()


def test_operation_identity_survives_sqlite_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "confirmed-state" / "continuity.json"
    repository = JsonFileReckoningRepository(state_path)
    application = build_application(tmp_path / "seed" / "continuity.json", "seed")
    reckoning = _proposal(application, "restart replay")
    repository.save(
        reckoning,
        expected_version=0,
        operation=_operation("op-3", result_id=reckoning.id),
    )

    reopened = JsonFileReckoningRepository(state_path)

    assert reopened.lookup_operation("op-3") == _operation(
        "op-3", result_id=reckoning.id
    )
    assert reopened.list_reckonings() == (reckoning,)


def test_additive_upgrade_adds_operation_storage_to_a_pre_change_installation(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "confirmed-state" / "continuity.json"
    repository = JsonFileReckoningRepository(state_path)
    with closing(connect_database(repository.database_path)) as connection:
        connection.execute("DROP TABLE continuity_operations")
        connection.commit()

    upgraded = JsonFileReckoningRepository(state_path)
    application = build_application(tmp_path / "seed" / "continuity.json", "seed")
    reckoning = _proposal(application, "additive upgrade")
    upgraded.save(
        reckoning,
        expected_version=0,
        operation=_operation("op-4", result_id=reckoning.id),
    )

    assert upgraded.lookup_operation("op-4") == _operation(
        "op-4", result_id=reckoning.id
    )
    assert upgraded.get(reckoning.id) == reckoning


def test_pre_change_operation_table_gains_binding_columns_additively(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "confirmed-state" / "continuity.json"
    repository = JsonFileReckoningRepository(state_path)
    with closing(connect_database(repository.database_path)) as connection:
        connection.execute("DROP TABLE continuity_operations")
        connection.execute(
            """
            CREATE TABLE continuity_operations (
                operation_id TEXT PRIMARY KEY,
                payload_digest TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
                result_id TEXT NOT NULL,
                pending_input TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )
            """
        )
        connection.commit()

    upgraded = JsonFileReckoningRepository(state_path)
    bound = OperationRecord(
        operation_id="op-bound",
        payload_digest="digest-bound",
        status="completed",
        result_id="decision-bound",
        pending_input="",
        occurred_at=NOW,
        kind="correct",
        target_id="decision-bound",
        displayed_revision=3,
        record_id="record-bound",
        correction="the corrected meaning",
        submission="No, correct it: the corrected meaning",
    )
    upgraded.record_operation(bound)

    assert upgraded.lookup_operation("op-bound") == bound


def test_legacy_continuity_migrates_without_confirming_proposals(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "continuity.json"
    application = build_application(tmp_path / "seed" / "continuity.json", "legacy")
    proposal = application.start_reckoning("legacy proposal")
    check_in = CheckIn("legacy-check", proposal.id, NOW, "legacy outcome", ())
    legacy = {
        "schema_version": 1,
        "reckonings": [asdict(proposal)],
        "check_ins": [asdict(check_in)],
    }
    state_path.write_text(
        json.dumps(legacy, default=lambda value: value.isoformat()), encoding="utf-8"
    )

    repository = JsonFileReckoningRepository(state_path)
    migrated = repository.get(proposal.id)

    assert migrated.status == "proposed"
    assert migrated.current_records[0].status == "proposed"
    assert migrated.id == proposal.id
    assert migrated.current_records[0].evidence_ids == (
        "evidence:legacy proposal",
    )
    assert repository.list_check_ins(proposal.id) == (check_in,)
    assert json.loads(state_path.read_text())["authority"] == "sqlite"


def test_interrupted_continuity_migration_rolls_back_and_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "continuity.json"
    application = build_application(tmp_path / "seed" / "continuity.json", "legacy")
    proposal = application.start_reckoning("interrupted proposal")
    legacy = {
        "schema_version": 1,
        "reckonings": [asdict(proposal)],
        "check_ins": [],
    }
    state_path.write_text(
        json.dumps(legacy, default=lambda value: value.isoformat()), encoding="utf-8"
    )
    import reckoning.persistence as persistence

    real_insert = persistence._insert_reckoning

    def fail_insert(connection: object, reckoning: Reckoning) -> None:
        del connection, reckoning
        raise OSError("simulated interrupted migration")

    monkeypatch.setattr(persistence, "_insert_reckoning", fail_insert)
    with pytest.raises(OSError, match="interrupted migration"):
        JsonFileReckoningRepository(state_path)
    monkeypatch.setattr(persistence, "_insert_reckoning", real_insert)

    restarted = JsonFileReckoningRepository(state_path)

    assert restarted.get(proposal.id).status == "proposed"
    assert restarted.database_path.is_file()


def test_clean_backup_restore_preserves_continuity_and_root_separation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    server = tmp_path / "server"
    restored = tmp_path / "restored"
    restored_server = tmp_path / "restored-server"
    archive = tmp_path / "continuity.reckoning"
    state_path = server / "confirmed-state" / "continuity.json"
    app = build_application(state_path, "server")
    decision = app.start_reckoning("server decision")
    app.confirm_reckoning(decision.id, expected_revision=decision.version)
    app.record_check_in(decision.id, "server outcome")
    source.mkdir()
    (source / "instance.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "placement_profile": "personal-server",
                "storage_roots": {
                    "local": "local-data-dir",
                    "server": str(server),
                },
            }
        ),
        encoding="utf-8",
    )

    create_transfer(
        source,
        archive,
        PASSPHRASE,
        kind="backup",
        server_data_dir=server,
    )
    restore_transfer(
        archive,
        restored,
        PASSPHRASE,
        server_data_dir=restored_server,
    )

    restored_repository = JsonFileReckoningRepository(
        restored_server / "confirmed-state" / "continuity.json"
    )
    assert restored_repository.get(decision.id).status == "confirmed"
    assert [
        item.outcome for item in restored_repository.list_check_ins(decision.id)
    ] == ["server outcome"]
    assert restored_repository.database_path.parent == restored_server
    assert not (restored / "reckoning.sqlite3").exists()


def test_restore_refuses_to_replace_a_newer_live_continuity_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    archive = tmp_path / "continuity.reckoning"
    build_application(source / "continuity.json", "source").start_reckoning("old")
    create_transfer(source, archive, PASSPHRASE, kind="backup")
    live = build_application(target / "continuity.json", "target")
    newer = live.start_reckoning("newer accepted state")
    live.confirm_reckoning(newer.id, expected_revision=newer.version)

    with pytest.raises(Exception, match="clean, empty"):
        restore_transfer(archive, target, PASSPHRASE)

    assert JsonFileReckoningRepository(
        target / "continuity.json"
    ).get(newer.id).status == "confirmed"


def test_mutation_save_is_atomic_with_its_replay_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "confirmed-state" / "continuity.json"
    application = build_application(state_path, "first")
    decision = application.start_reckoning("atomic")

    import reckoning.persistence as persistence

    real_upsert = persistence._upsert_operation

    def failing_upsert(connection: object, operation: object) -> None:
        del connection, operation
        raise RuntimeError("simulated operation storage fault")

    monkeypatch.setattr(persistence, "_upsert_operation", failing_upsert)

    with pytest.raises(RuntimeError, match="simulated operation storage fault"):
        application.confirm_reckoning(
            decision.id, expected_revision=decision.version, operation_id="op-fault"
        )

    monkeypatch.setattr(persistence, "_upsert_operation", real_upsert)

    reopened = build_application(state_path, "second")
    assert reopened.inspect_reckoning(decision.id).status == "proposed"
    assert reopened._dependencies.reckoning_repository.lookup_operation("op-fault") is None
