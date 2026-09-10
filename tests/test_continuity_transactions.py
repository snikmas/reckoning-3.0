from __future__ import annotations

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
    PersonalRecordProposal,
    Reckoning,
    ReckoningDraft,
    ReckoningRevisionConflict,
    SourcedFact,
)
from reckoning.operations import create_transfer, restore_transfer
from reckoning.persistence import JsonFileReckoningRepository


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
