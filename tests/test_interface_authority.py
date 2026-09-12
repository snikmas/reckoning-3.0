from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import threading

import pytest

from reckoning.interfaces import (
    InterfaceProjectionConflict,
    InterfaceSessionConflict,
    JsonFileInterfaceRepository,
    OperationalFailure,
    ReckoningInterfaceApplication,
    RunReceipt,
    create_local_interface_application,
)
from reckoning.operations import create_transfer, restore_transfer, setup_instance
from reckoning.root_database import ROOT_DATABASE_FILENAME


NOW = datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc)
PASSPHRASE = "correct-horse-battery-staple"


class StubResponder:
    def __init__(self, response: str = "Synthetic reply.") -> None:
        self.response = response
        self.requests: list[object] = []

    def respond(self, request: object) -> str:
        self.requests.append(request)
        return self.response


class StubMessageApplication:
    def respond_with_channel_context(
        self,
        text: str,
        history: tuple[tuple[str, str], ...],
        confirmed_records: tuple[str, ...],
        permissions: tuple[str, ...],
        **kwargs: object,
    ) -> object:
        del text, history, confirmed_records, permissions, kwargs

        class Response:
            content = "Synthetic reply."

        return Response()


def _interface(tmp_path: Path) -> ReckoningInterfaceApplication:
    return ReckoningInterfaceApplication(
        repository=JsonFileInterfaceRepository(tmp_path / "interfaces.json"),
        responder=StubResponder(),
    )


def test_independent_repositories_append_web_and_telegram_turns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "interfaces.json"
    web = JsonFileInterfaceRepository(path)
    telegram = JsonFileInterfaceRepository(path)

    web.append_completed_turn("web", "", "web question", "web reply", expected_revision=0)
    telegram.append_completed_turn(
        "telegram", "42", "telegram question", "telegram reply", expected_revision=0
    )

    reopened = JsonFileInterfaceRepository(path)
    assert [
        (session.channel, session.session_id)
        for session in reopened.load().sessions
    ] == [("web", ""), ("telegram", "42")]
    assert reopened.load().returning_user is True


def test_same_session_stale_revision_conflicts_but_other_sessions_succeed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "interfaces.json"
    repository = JsonFileInterfaceRepository(path)
    repository.append_completed_turn(
        "web", "", "first", "reply", expected_revision=0
    )

    with pytest.raises(InterfaceSessionConflict) as conflict:
        repository.append_completed_turn(
            "web", "", "stale", "reply", expected_revision=0
        )
    assert conflict.value.expected == 0
    assert conflict.value.actual == 1

    repository.append_completed_turn(
        "telegram", "42", "other session", "reply", expected_revision=0
    )
    assert len(repository.load().sessions) == 2


def test_independent_projection_receipt_failure_and_approval_updates(
    tmp_path: Path,
) -> None:
    repository = JsonFileInterfaceRepository(tmp_path / "interfaces.json")

    repository.replace_projection(
        "matters_now",
        ("Protect the exam baseline.",),
        expected_revision=repository.projection_revision("matters_now"),
    )
    repository.replace_projection(
        "changes",
        ("One material change.",),
        expected_revision=repository.projection_revision("changes"),
    )
    with pytest.raises(InterfaceProjectionConflict):
        repository.replace_projection(
            "matters_now", ("Stale value.",), expected_revision=0
        )

    receipt = RunReceipt(
        id="run-1",
        occurred_at=NOW,
        status="succeeded",
        summary="Synthetic run",
    )
    failure = OperationalFailure("failure-1", "run-1", "Synthetic failure", NOW)
    repository.record_operational_run(receipt, failure=failure)

    repository.add_pending_approval("approval-1")
    assert repository.resolve_approval("approval-1") is True
    assert repository.resolve_approval("approval-1") is False

    reopened = JsonFileInterfaceRepository(tmp_path / "interfaces.json")
    state = reopened.load()
    assert state.matters_now == ("Protect the exam baseline.",)
    assert state.changes == ("One material change.",)
    assert state.receipts == (receipt,)
    assert state.failures == (failure,)
    assert state.pending_approvals == ()


def test_receipt_upsert_requires_the_expected_revision(tmp_path: Path) -> None:
    repository = JsonFileInterfaceRepository(tmp_path / "interfaces.json")
    receipt = RunReceipt("run-1", NOW, "succeeded", "first")
    repository.record_operational_run(receipt, expected_revision=0)
    with pytest.raises(InterfaceProjectionConflict):
        repository.record_operational_run(
            RunReceipt("run-1", NOW, "failed", "stale"), expected_revision=0
        )


def test_legacy_interfaces_migrate_once_with_rollback(tmp_path: Path) -> None:
    path = tmp_path / "interfaces.json"
    legacy = {
        "schema_version": 1,
        "state": {
            "returning_user": True,
            "matters_now": ["Keep the baseline."],
            "sessions": [
                {
                    "channel": "web",
                    "session_id": "",
                    "messages": [
                        {"role": "user", "content": "Legacy question"},
                        {"role": "assistant", "content": "Legacy reply"},
                    ],
                }
            ],
        },
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")

    repository = JsonFileInterfaceRepository(path)
    state = repository.load()
    assert state.returning_user is True
    assert state.matters_now == ("Keep the baseline.",)
    assert state.sessions[0].messages[0].content == "Legacy question"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": 2,
        "authority": "sqlite",
        "database": ROOT_DATABASE_FILENAME,
        "migration_state": "sqlite-authoritative",
    }
    rollback = path.with_name("interfaces.rollback.json")
    assert json.loads(rollback.read_text(encoding="utf-8")) == legacy

    restarted = JsonFileInterfaceRepository(path)
    assert restarted.load().sessions[0].messages[1].content == "Legacy reply"


def test_invalid_legacy_interfaces_do_not_activate_authority(tmp_path: Path) -> None:
    path = tmp_path / "interfaces.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "state": {
                    "sessions": [
                        {"channel": "web", "session_id": "", "messages": []},
                        {"channel": "web", "session_id": "", "messages": []},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Invalid legacy interface"):
        JsonFileInterfaceRepository(path)


def test_interrupted_migration_recovers_from_the_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import reckoning.interface_store as store

    path = tmp_path / "interfaces.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "state": {
                    "sessions": [
                        {
                            "channel": "web",
                            "session_id": "",
                            "messages": [
                                {"role": "user", "content": "Recover me"},
                            ],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    original = store._merge_state
    calls = {"count": 0}

    def flaky(connection, state):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("interrupted migration")
        return original(connection, state)

    monkeypatch.setattr(store, "_merge_state", flaky)
    with pytest.raises(RuntimeError, match="interrupted migration"):
        JsonFileInterfaceRepository(path)

    repository = JsonFileInterfaceRepository(path)
    assert repository.load().sessions[0].messages[0].content == "Recover me"


def test_concurrent_first_initialization_is_safe(tmp_path: Path) -> None:
    path = tmp_path / "interfaces.json"
    barrier = threading.Barrier(6)

    def initialize() -> None:
        barrier.wait()
        JsonFileInterfaceRepository(path)

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(lambda _index: initialize(), range(6)))

    JsonFileInterfaceRepository(path).append_completed_turn(
        "web", "", "one", "reply", expected_revision=0
    )
    assert JsonFileInterfaceRepository(path).load().sessions[0].channel == "web"


def test_public_composition_paths_reopen_the_same_state(tmp_path: Path) -> None:
    interface = create_local_interface_application(
        StubMessageApplication(), tmp_path / "interfaces.json"
    )
    interface.send_channel_message("web", "Persist this web turn.")

    reopened = JsonFileInterfaceRepository(tmp_path / "interfaces.json")
    messages = reopened.load().sessions[0].messages
    assert [(message.role, message.content) for message in messages] == [
        ("user", "Persist this web turn."),
        ("assistant", "Synthetic reply."),
    ]


def test_clean_encrypted_restore_preserves_interface_state(tmp_path: Path) -> None:
    source = tmp_path / "source"
    restored = tmp_path / "restored"
    archive = tmp_path / "state.reckoning"
    setup_instance(source, "local")
    repository = JsonFileInterfaceRepository(
        source / "confirmed-state" / "interfaces.json"
    )
    repository.append_completed_turn(
        "telegram", "mary", "Before backup", "Before backup reply", expected_revision=0
    )
    repository.replace_projection(
        "matters_now",
        ("Backed up matter.",),
        expected_revision=repository.projection_revision("matters_now"),
    )

    create_transfer(source, archive, PASSPHRASE, kind="backup")
    restore_transfer(archive, restored, PASSPHRASE)

    reopened = JsonFileInterfaceRepository(
        restored / "confirmed-state" / "interfaces.json"
    ).load()
    assert reopened.returning_user is True
    assert reopened.matters_now == ("Backed up matter.",)
    assert reopened.sessions[0].session_id == "mary"
    assert reopened.sessions[0].messages[0].content == "Before backup"


def test_clean_encrypted_restore_preserves_personal_server_placement(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    server_source = tmp_path / "server-source"
    restored = tmp_path / "restored"
    server_restored = tmp_path / "server-restored"
    archive = tmp_path / "state.reckoning"
    setup_instance(source, "hybrid", server_data_dir=server_source)
    repository = JsonFileInterfaceRepository(
        source / "confirmed-state" / "interfaces.json"
    )
    repository.append_completed_turn(
        "web", "", "Server placement turn", "reply", expected_revision=0
    )

    create_transfer(
        source, archive, PASSPHRASE, kind="backup", server_data_dir=server_source
    )
    restore_transfer(
        archive, restored, PASSPHRASE, server_data_dir=server_restored
    )

    reopened = JsonFileInterfaceRepository(
        restored / "confirmed-state" / "interfaces.json"
    ).load()
    assert reopened.sessions[0].messages[0].content == "Server placement turn"


def test_validate_database_accepts_interface_tables(tmp_path: Path) -> None:
    from reckoning.model_run_store import validate_database

    repository = JsonFileInterfaceRepository(tmp_path / "interfaces.json")
    repository.append_completed_turn("web", "", "one", "reply", expected_revision=0)
    validate_database(tmp_path / ROOT_DATABASE_FILENAME)
