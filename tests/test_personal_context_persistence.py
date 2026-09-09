from __future__ import annotations

import json
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from reckoning.application import (
    ApplicationDependencies,
    InMemoryConversationStorage,
    ModelRequest,
    NoConnectors,
    PlacementState,
    ReckoningApplication,
)
from reckoning.operations import (
    OperationError,
    create_transfer,
    load_installation_runtime,
    restore_transfer,
    setup_instance,
)
from reckoning.personal_context import (
    ContextRevisionConflict,
    JsonFilePersonalContextRepository,
    PersonalContextService,
)
from reckoning.root_database import ROOT_DATABASE_FILENAME

NOW = datetime(2026, 9, 6, 14, 0, tzinfo=UTC)
PASSPHRASE = "synthetic-personal-context-passphrase"


class FixedClock:
    def now(self) -> datetime:
        return NOW


class RecordingModel:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def respond(self, request: ModelRequest) -> str:
        self.requests.append(request)
        return "Synthetic profile-aware reply."


def _build_application(path: Path, model: RecordingModel) -> ReckoningApplication:
    return ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(),
            model=model,
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            personal_context=PersonalContextService(
                JsonFilePersonalContextRepository(path)
            ),
        )
    )


def _retrieved_context(model: RecordingModel) -> str:
    request = model.requests[-1]
    return next(
        layer.content
        for layer in request.prompt_stack.layers
        if layer.name == "retrieved_context"
    )


def _legacy_version(
    record_id: str,
    version: int,
    *,
    text: str,
    language: str,
    source: str,
    processing_location: str,
    status: str = "active",
    retrieval_permitted: bool = True,
) -> dict[str, object]:
    return {
        "record_id": record_id,
        "version": version,
        "status": status,
        "original_text": text,
        "language": language,
        "canonical_meaning": text,
        "source": source,
        "sensitivity": "private",
        "retrieval_permitted": retrieval_permitted,
        "processing_location": processing_location,
        "created_at": (NOW + timedelta(minutes=version)).isoformat(),
        "fresh_until": None,
        "retention_until": None,
        "supersedes_version": version - 1 or None,
    }


def test_deletion_remains_authoritative_after_another_application_writes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "personal-context" / "personal-context.json"
    seed = PersonalContextService(JsonFilePersonalContextRepository(path))
    seed.remember(
        record_id="delete-me",
        original_text="My private study note is cedar.",
        language="en",
        canonical_meaning="Mary's private study note is cedar.",
        source="direct user statement",
        created_at=NOW,
        sensitivity="private",
    )
    seed.propose(
        record_id="keep-me",
        original_text="I may study databases next.",
        language="en",
        canonical_meaning="Mary may study databases next.",
        source="synthetic profile",
        created_at=NOW,
    )
    deleting_model = RecordingModel()
    stale_model = RecordingModel()
    deleting_application = _build_application(path, deleting_model)
    already_running_application = _build_application(path, stale_model)

    deleting_application.delete_personal_context("delete-me")
    already_running_application.correct_profile_proposal(
        "keep-me",
        "I currently study database transactions.",
        language="en",
    )
    already_running_application.send_message(
        "What is my private study note and what am I studying?"
    )

    outgoing_context = _retrieved_context(stale_model)
    assert "cedar" not in outgoing_context
    assert "database transactions" in outgoing_context

    restarted_model = RecordingModel()
    restarted = _build_application(path, restarted_model)
    restarted.send_message("What is my private study note?")
    assert "cedar" not in _retrieved_context(restarted_model)
    with pytest.raises(KeyError, match="delete-me"):
        PersonalContextService(JsonFilePersonalContextRepository(path)).inspect(
            "delete-me"
        )
    with pytest.raises(KeyError, match="Permanently deleted"):
        seed.remember(
            record_id="delete-me",
            original_text="Try to restore cedar.",
            language="en",
            canonical_meaning="Try to restore cedar.",
            source="synthetic profile",
            created_at=NOW + timedelta(minutes=1),
        )
    assert [
        marker.deleted_record_id
        for marker in JsonFilePersonalContextRepository(path).suppression_markers()
    ] == ["delete-me"]


def test_stale_multilingual_correction_returns_an_explicit_revision_conflict(
    tmp_path: Path,
) -> None:
    path = tmp_path / "personal-context" / "personal-context.json"
    seed = PersonalContextService(JsonFilePersonalContextRepository(path))
    proposal = seed.propose(
        record_id="language-choice",
        original_text="I study Chinese on Tuesday.",
        language="en",
        canonical_meaning="Mary studies Chinese on Tuesday.",
        source="synthetic profile",
        created_at=NOW,
    )
    first = _build_application(path, RecordingModel())
    second = _build_application(path, RecordingModel())

    corrected = first.correct_profile_proposal(
        proposal.record_id,
        "Теперь я занимаюсь китайским по средам.",
        language="ru",
        expected_revision=proposal.version,
    )
    with pytest.raises(ContextRevisionConflict) as conflict:
        second.correct_profile_proposal(
            proposal.record_id,
            "我星期四学中文。",
            language="zh",
            expected_revision=proposal.version,
        )

    assert conflict.value.expected == 1
    assert conflict.value.actual == 2
    restarted = PersonalContextService(JsonFilePersonalContextRepository(path))
    history = restarted.inspect(proposal.record_id)
    assert history.current == corrected
    assert history.current.language == "ru"
    assert history.current.source == "direct user correction"
    assert history.current.supersedes_version == 1
    assert history.superseded == (proposal,)


def test_legacy_context_migrates_once_and_resumes_incomplete_initialization(
    tmp_path: Path,
) -> None:
    root = tmp_path / "installation"
    path = root / "personal-context" / "personal-context.json"
    path.parent.mkdir(parents=True)
    legacy = {
        "schema_version": 1,
        "versions": [
            _legacy_version(
                "kept",
                1,
                text="I study Chinese on Tuesday.",
                language="en",
                source="direct user statement",
                processing_location="local",
            ),
            _legacy_version(
                "excluded",
                1,
                text="An unconfirmed private profile statement.",
                language="en",
                source="synthetic profile",
                processing_location="local",
                status="proposed",
                retrieval_permitted=False,
            ),
            _legacy_version(
                "kept",
                2,
                text="Теперь я занимаюсь китайским по средам.",
                language="ru",
                source="direct user correction",
                processing_location="local",
            ),
        ],
        "suppressions": [
            {"deleted_record_id": "deleted", "deleted_at": NOW.isoformat()}
        ],
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")
    database = root / ROOT_DATABASE_FILENAME
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE root_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO root_metadata VALUES ('root_schema_version', '1')"
        )

    repository = JsonFilePersonalContextRepository(path)
    service = PersonalContextService(repository)

    history = service.inspect("kept")
    assert [item.original_text for item in (*history.superseded, history.current)] == [
        "I study Chinese on Tuesday.",
        "Теперь я занимаюсь китайским по средам.",
    ]
    assert history.current.source == "direct user correction"
    assert history.current.processing_location == "local"
    assert [item.record_id for item in service.list_proposed()] == ["excluded"]
    assert service.list_proposed()[0].retrieval_permitted is False
    assert [
        marker.deleted_record_id for marker in repository.suppression_markers()
    ] == ["deleted"]
    assert repository.database_path == database
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": 2,
        "authority": "sqlite",
        "database": ROOT_DATABASE_FILENAME,
        "migration_state": "sqlite-authoritative",
    }
    rollback = path.with_name("personal-context.rollback.json")
    assert json.loads(rollback.read_text(encoding="utf-8")) == legacy
    assert not rollback.stat().st_mode & stat.S_IWUSR

    restarted = PersonalContextService(JsonFilePersonalContextRepository(path))
    assert restarted.inspect("kept") == history

    restarted.delete("kept", NOW + timedelta(hours=1))
    sanitized_rollback = json.loads(rollback.read_text(encoding="utf-8"))
    assert [item["record_id"] for item in sanitized_rollback["versions"]] == [
        "excluded"
    ]
    assert [
        item["deleted_record_id"] for item in sanitized_rollback["suppressions"]
    ] == ["deleted", "kept"]
    assert "Теперь" not in rollback.read_text(encoding="utf-8")


def test_invalid_legacy_context_does_not_activate_sqlite_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "personal-context.json"
    duplicate = _legacy_version(
        "duplicate",
        1,
        text="Synthetic context.",
        language="en",
        source="synthetic profile",
        processing_location="local",
    )
    legacy = {
        "schema_version": 1,
        "versions": [duplicate, duplicate],
        "suppressions": [],
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Invalid legacy personal-context"):
        JsonFilePersonalContextRepository(path)

    assert not (tmp_path / ROOT_DATABASE_FILENAME).exists()
    assert json.loads(path.read_text(encoding="utf-8")) == legacy


def test_server_placement_backup_preserves_deletion_and_rejects_old_rollback(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source_server = tmp_path / "source-server"
    setup_instance(source, "personal-server", server_data_dir=source_server)
    runtime = load_installation_runtime(source, server_data_dir=source_server)
    path = runtime.state_path("personal-context", "personal-context.json")
    repository = JsonFilePersonalContextRepository(path)
    service = PersonalContextService(repository)
    service.remember(
        record_id="delete-after-backup",
        original_text="This synthetic context will be deleted.",
        language="en",
        canonical_meaning="This synthetic context will be deleted.",
        source="direct user statement",
        created_at=NOW,
        processing_location="personal-server",
    )
    older_archive = tmp_path / "older.reckoning"
    create_transfer(
        source,
        older_archive,
        PASSPHRASE,
        kind="backup",
        server_data_dir=source_server,
    )
    service.delete("delete-after-backup", NOW + timedelta(minutes=1))

    with pytest.raises(OperationError, match="clean, empty"):
        restore_transfer(
            older_archive,
            source,
            PASSPHRASE,
            server_data_dir=source_server,
        )
    assert [
        marker.deleted_record_id for marker in repository.suppression_markers()
    ] == ["delete-after-backup"]

    current_archive = tmp_path / "current.reckoning"
    create_transfer(
        source,
        current_archive,
        PASSPHRASE,
        kind="backup",
        server_data_dir=source_server,
    )
    restored = tmp_path / "restored"
    restored_server = tmp_path / "restored-server"
    restore_transfer(
        current_archive,
        restored,
        PASSPHRASE,
        server_data_dir=restored_server,
    )
    restored_runtime = load_installation_runtime(
        restored, server_data_dir=restored_server
    )
    restored_path = restored_runtime.state_path(
        "personal-context", "personal-context.json"
    )
    restored_repository = JsonFilePersonalContextRepository(restored_path)
    assert restored_repository.all_versions() == ()
    assert [
        marker.deleted_record_id for marker in restored_repository.suppression_markers()
    ] == ["delete-after-backup"]
    assert not (restored / ROOT_DATABASE_FILENAME).exists()
    assert (restored_server / ROOT_DATABASE_FILENAME).is_file()


def test_transfer_rejects_an_incomplete_personal_context_schema(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    setup_instance(source, "local")
    runtime = load_installation_runtime(source)
    repository = JsonFilePersonalContextRepository(
        runtime.state_path("personal-context", "personal-context.json")
    )
    database = repository.database_path
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE personal_context_suppressions")

    with pytest.raises(OperationError, match="invalid placement-root database"):
        create_transfer(
            source,
            tmp_path / "invalid.reckoning",
            PASSPHRASE,
            kind="backup",
        )
