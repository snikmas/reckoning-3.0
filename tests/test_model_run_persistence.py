import json
import sqlite3
import stat
from datetime import UTC, datetime
from multiprocessing import get_context
from pathlib import Path
from typing import Any

import pytest

from reckoning.application import (
    ApplicationDependencies,
    DeterministicFakeModel,
    InMemoryConversationStorage,
    ModelRunRecord,
    NoConnectors,
    PlacementState,
    ReckoningApplication,
    SystemClock,
    create_local_application,
)
from reckoning.model_run_store import ROOT_DATABASE_FILENAME
from reckoning.operations import (
    create_transfer,
    load_installation_runtime,
    restore_transfer,
    setup_instance,
)

PASSPHRASE = "test-passphrase-for-receipts"


def _legacy_run(
    run_id: str,
    *,
    status: str = "succeeded",
    failure: str | None = None,
) -> dict[str, object]:
    return {
        "id": run_id,
        "requested_at": "2026-09-06T12:34:56+00:00",
        "status": status,
        "provider": "fake",
        "model": "deterministic-fake",
        "model_calls": 2,
        "latency_ms": 17,
        "retries": 1,
        "input_tokens": 11,
        "output_tokens": 7,
        "billable_units": 18,
        "failure": failure,
    }


def _record_run_in_process(state_path: str, message: str, start: Any) -> None:
    start.wait()
    application = create_local_application(Path(state_path), provider_name="fake")
    application.send_message(message)


class RefusingModelRunRepository:
    def save_run(self, run: ModelRunRecord) -> None:
        raise OSError("receipt storage unavailable")

    def list_runs(self) -> tuple[ModelRunRecord, ...]:
        return ()


def test_independent_applications_preserve_each_model_run(tmp_path: Path) -> None:
    state_path = tmp_path / "continuity.json"
    first = create_local_application(state_path, provider_name="fake")
    second = create_local_application(state_path, provider_name="fake")

    first.send_message("Record the first bounded run.")
    second.send_message("Record the second bounded run.")

    runs = create_local_application(
        state_path, provider_name="fake"
    ).inspect_model_runs()
    assert len(runs) == 2
    assert len({run.id for run in runs}) == 2
    assert [run.status for run in runs] == ["succeeded", "succeeded"]
    assert [run.provider for run in runs] == ["fake", "fake"]
    assert [run.usage_status for run in runs] == ["not-billable", "not-billable"]
    assert len(first.inspect_model_runs()) == 2


def test_processes_initialize_and_append_to_one_placement_database(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "continuity.json"
    context = get_context("spawn")
    start = context.Barrier(3)
    processes = [
        context.Process(
            target=_record_run_in_process,
            args=(str(state_path), f"Run from process {index}.", start),
        )
        for index in range(3)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)

    assert [process.exitcode for process in processes] == [0, 0, 0]
    runs = create_local_application(
        state_path, provider_name="fake"
    ).inspect_model_runs()
    assert len(runs) == 3
    assert len({run.id for run in runs}) == 3


def test_application_does_not_return_completion_before_receipt_persists() -> None:
    storage = InMemoryConversationStorage()
    application = ReckoningApplication(
        ApplicationDependencies(
            clock=SystemClock(),
            model=DeterministicFakeModel(),
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=storage,
            model_runs=RefusingModelRunRepository(),
        )
    )

    with pytest.raises(OSError, match="receipt storage unavailable"):
        application.send_message("Do not claim this completed.")

    assert storage.list_messages() == ()


def test_existing_receipts_migrate_once_without_losing_fields(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    state_path = source / "continuity.json"
    legacy_path = source / "model-runs.json"
    legacy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": [
                    _legacy_run("run-succeeded"),
                    _legacy_run(
                        "run-failed",
                        status="failed",
                        failure="Provider outcome and cost are uncertain.",
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )

    runs = create_local_application(
        state_path, provider_name="fake"
    ).inspect_model_runs()

    assert [run.__dict__ for run in runs] == [
        {
            "id": "run-succeeded",
            "requested_at": datetime(2026, 9, 6, 12, 34, 56, tzinfo=UTC),
            "status": "succeeded",
            "provider": "fake",
            "model": "deterministic-fake",
            "model_calls": 2,
            "latency_ms": 17,
            "retries": 1,
            "input_tokens": 11,
            "output_tokens": 7,
            "billable_units": 18,
            "usage_status": "reported",
            "failure": None,
        },
        {
            "id": "run-failed",
            "requested_at": datetime(2026, 9, 6, 12, 34, 56, tzinfo=UTC),
            "status": "failed",
            "provider": "fake",
            "model": "deterministic-fake",
            "model_calls": 2,
            "latency_ms": 17,
            "retries": 1,
            "input_tokens": 11,
            "output_tokens": 7,
            "billable_units": 18,
            "usage_status": "reported",
            "failure": "Provider outcome and cost are uncertain.",
        },
    ]
    assert (source / ROOT_DATABASE_FILENAME).is_file()
    assert not (source / ROOT_DATABASE_FILENAME).stat().st_mode & stat.S_IRWXG
    assert not (source / ROOT_DATABASE_FILENAME).stat().st_mode & stat.S_IRWXO
    rollback = source / "model-runs.rollback.json"
    assert rollback.is_file()
    assert not rollback.stat().st_mode & stat.S_IWUSR
    legacy_authority = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert legacy_authority == {
        "schema_version": 2,
        "authority": "sqlite",
        "database": ROOT_DATABASE_FILENAME,
        "migration_state": "sqlite-authoritative",
    }

    restarted = create_local_application(state_path, provider_name="fake")
    assert [run.id for run in restarted.inspect_model_runs()] == [
        "run-succeeded",
        "run-failed",
    ]
    restarted.send_message("Persist a receipt created after migration.")
    expected = restarted.inspect_model_runs()
    assert len(expected) == 3

    archive = tmp_path / "migrated-backup.reckoning"
    create_transfer(source, archive, PASSPHRASE, kind="backup")
    restored = tmp_path / "restored-migration"
    restore_transfer(archive, restored, PASSPHRASE)
    assert (
        create_local_application(
            restored / "continuity.json", provider_name="fake"
        ).inspect_model_runs()
        == expected
    )


def test_invalid_legacy_receipts_do_not_activate_a_database(tmp_path: Path) -> None:
    legacy_path = tmp_path / "model-runs.json"
    duplicate = _legacy_run("duplicate")
    legacy_path.write_text(
        json.dumps({"schema_version": 1, "runs": [duplicate, duplicate]}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Invalid legacy model-run storage"):
        create_local_application(tmp_path / "continuity.json", provider_name="fake")

    assert not (tmp_path / ROOT_DATABASE_FILENAME).exists()
    assert json.loads(legacy_path.read_text(encoding="utf-8"))["runs"] == [
        duplicate,
        duplicate,
    ]


def test_interrupted_migration_resumes_from_the_legacy_authority(
    tmp_path: Path,
) -> None:
    legacy_path = tmp_path / "model-runs.json"
    legacy_path.write_text(
        json.dumps({"schema_version": 1, "runs": [_legacy_run("run-resumed")]}),
        encoding="utf-8",
    )
    database = tmp_path / ROOT_DATABASE_FILENAME
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE root_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO root_metadata VALUES ('root_schema_version', '1')"
        )

    runs = create_local_application(
        tmp_path / "continuity.json", provider_name="fake"
    ).inspect_model_runs()

    assert [run.id for run in runs] == ["run-resumed"]


def test_encrypted_backup_clean_restore_preserves_receipts(tmp_path: Path) -> None:
    source = tmp_path / "source"
    setup_instance(source, "local")
    runtime = load_installation_runtime(source)
    application = create_local_application(
        runtime.state_path("confirmed-state", "continuity.json"),
        provider_name="fake",
    )
    application.send_message("Preserve this completed run through recovery.")
    expected = application.inspect_model_runs()

    archive = tmp_path / "backup.reckoning"
    create_transfer(source, archive, PASSPHRASE, kind="backup")
    restored = tmp_path / "restored"
    restore_transfer(archive, restored, PASSPHRASE)

    restored_runtime = load_installation_runtime(restored)
    restored_application = create_local_application(
        restored_runtime.state_path("confirmed-state", "continuity.json"),
        provider_name="fake",
    )
    assert restored_application.inspect_model_runs() == expected


def test_separate_server_root_keeps_and_restores_its_receipts(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source_server = tmp_path / "source-server"
    setup_instance(source, "personal-server", server_data_dir=source_server)
    runtime = load_installation_runtime(source, server_data_dir=source_server)
    application = create_local_application(
        runtime.state_path("confirmed-state", "continuity.json"),
        provider_name="fake",
    )
    application.send_message("Keep this run in the server placement root.")
    expected = application.inspect_model_runs()

    assert not (source / ROOT_DATABASE_FILENAME).exists()
    assert (source_server / ROOT_DATABASE_FILENAME).is_file()
    archive = tmp_path / "server-backup.reckoning"
    create_transfer(
        source,
        archive,
        PASSPHRASE,
        kind="backup",
        server_data_dir=source_server,
    )

    restored = tmp_path / "restored"
    restored_server = tmp_path / "restored-server"
    restore_transfer(
        archive,
        restored,
        PASSPHRASE,
        server_data_dir=restored_server,
    )
    restored_runtime = load_installation_runtime(
        restored, server_data_dir=restored_server
    )
    restored_application = create_local_application(
        restored_runtime.state_path("confirmed-state", "continuity.json"),
        provider_name="fake",
    )

    assert not (restored / ROOT_DATABASE_FILENAME).exists()
    assert (restored_server / ROOT_DATABASE_FILENAME).is_file()
    assert restored_application.inspect_model_runs() == expected
    restored_instance = json.loads(
        (restored / "instance.json").read_text(encoding="utf-8")
    )
    assert restored_instance["storage_roots"]["server"] == str(
        restored_server.resolve()
    )
