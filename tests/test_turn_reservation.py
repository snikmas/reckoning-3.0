"""Tests for durable turn reservation and legacy notice migration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from reckoning.conversation import ContextStatus, split_legacy_notice_prefix
from reckoning.interfaces import (
    ChannelMessage,
    ChannelResponse,
    InMemoryInterfaceRepository,
    PlacementPolicy,
    ReckoningInterfaceApplication,
    SourcePlacement,
)
from reckoning.interface_store import SQLiteInterfaceRepository


class _FixedTurnClock:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value


def _local_policy() -> PlacementPolicy:
    return PlacementPolicy(
        profile="local",
        categories=(
            SourcePlacement(
                category="personal-context",
                sensitivity="private",
                storage_node="local",
                processing_node="local",
            ),
        ),
        local_node_available=True,
        server_node_available=False,
    )


class FailingResponder:
    def __init__(self, fail_count: int = 1) -> None:
        self._fail_count = fail_count
        self.attempts = 0

    def respond(self, request: object) -> ChannelResponse:
        self.attempts += 1
        if self.attempts <= self._fail_count:
            raise RuntimeError("provider failure")
        return ChannelResponse(speech="recovered", notices=())


class FixedResponder:
    def __init__(self, response: str = "ok") -> None:
        self.response = response

    def respond(self, request: object) -> ChannelResponse:
        return ChannelResponse(
            speech=self.response,
            notices=("Limited context: unavailable category.",),
            context_status=ContextStatus(
                used=("current-request",),
                unavailable=("personal-context",),
                excluded=(),
            ),
        )


def test_user_speech_is_reserved_before_provider_work() -> None:
    repository = InMemoryInterfaceRepository()
    interface = ReckoningInterfaceApplication(
        repository=repository,
        responder=FailingResponder(fail_count=1),
        placement=_local_policy(),
    )

    with pytest.raises(RuntimeError, match="provider failure"):
        interface.send_channel_message("web", "hello")

    turns = repository.list_turns("web")
    assert len(turns) == 1
    assert turns[0].user_speech == "hello"
    assert turns[0].state == "failed"
    assert turns[0].assistant_speech is None
    messages = interface.channel_session("web")
    assert messages == ()


def test_completed_turn_stores_notices_and_context_status() -> None:
    repository = InMemoryInterfaceRepository()
    interface = ReckoningInterfaceApplication(
        repository=repository,
        responder=FixedResponder(),
        placement=_local_policy(),
    )

    reply = interface.send_channel_message("web", "hello")

    assert reply.text == "ok"
    assert reply.notices == ("Limited context: unavailable category.",)
    turns = repository.list_turns("web")
    assert len(turns) == 1
    assert turns[0].state == "completed"
    assert turns[0].assistant_speech == "ok"
    assert turns[0].notices == ("Limited context: unavailable category.",)
    assert turns[0].context_status is not None
    assert turns[0].context_status.unavailable == ("personal-context",)


def test_legacy_notice_prefix_split() -> None:
    speech, notices = split_legacy_notice_prefix(
        "Limited context: no user profile is available. Hello."
    )
    assert speech == "Hello."
    assert notices == ("Limited context: no user profile is available.",)


def test_unknown_limited_context_prose_is_preserved_as_speech() -> None:
    text = "Limited context: I was explaining an unrelated limitation of the tool."
    speech, notices = split_legacy_notice_prefix(text)
    assert speech == text
    assert notices == ()


def test_known_generated_limited_context_notices_split() -> None:
    for notice in (
        "Limited context: no user profile is available.",
        "Limited context: unavailable processing categories: personal-context.",
        "Limited context: 1 earlier turn was omitted to fit the model context window.",
        "Limited context: 3 earlier turns were omitted to fit the model context window.",
    ):
        speech, notices = split_legacy_notice_prefix(f"{notice} Hello.")
        assert notices == (notice,), notice
        assert speech == "Hello."


def test_turn_repositories_honor_one_injected_clock(tmp_path: Path) -> None:
    fixed = datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)
    clock = _FixedTurnClock(fixed)
    memory = InMemoryInterfaceRepository(clock=clock)
    sqlite = SQLiteInterfaceRepository(tmp_path / "interfaces.json", clock=clock)

    results = []
    for repository in (memory, sqlite):
        session = repository.create_session("web", None, "user-created", clock.now())
        repository.reserve_turn(
            "web", session.session_id, "exact input", "turn-1", expected_revision=1
        )
        results.append(
            repository.complete_turn("web", session.session_id, "turn-1", "reply")
        )

    def fields(turn: object) -> tuple[object, ...]:
        return (
            turn.user_speech,  # type: ignore[attr-defined]
            turn.assistant_speech,  # type: ignore[attr-defined]
            turn.state,  # type: ignore[attr-defined]
            turn.created_at,  # type: ignore[attr-defined]
            turn.completed_at,  # type: ignore[attr-defined]
        )

    assert fields(results[0]) == fields(results[1])
    assert results[0].user_speech == "exact input"
    assert results[0].created_at == fixed
    assert results[0].completed_at == fixed


def test_sqlite_failed_turn_is_hydrated_from_persisted_values(
    tmp_path: Path,
) -> None:
    fixed = datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)
    clock = _FixedTurnClock(fixed)
    repository = SQLiteInterfaceRepository(tmp_path / "interfaces.json", clock=clock)
    session = repository.create_session("web", None, "user-created", clock.now())
    repository.reserve_turn(
        "web", session.session_id, "kept input", "turn-1", expected_revision=1
    )

    failed = repository.fail_turn("web", session.session_id, "turn-1")

    assert failed.user_speech == "kept input"
    assert failed.created_at == fixed
    assert failed.completed_at == fixed
    assert failed.state == "failed"


def test_unknown_historical_text_preserved() -> None:
    speech, notices = split_legacy_notice_prefix("Limited edition watch.")
    assert speech == "Limited edition watch."
    assert notices == ()


def test_sqlite_migration_splits_legacy_notice_prefix(tmp_path: Path) -> None:
    import sqlite3

    legacy_path = tmp_path / "interfaces.json"
    db_path = tmp_path / "reckoning.sqlite3"
    connection = sqlite3.connect(str(db_path))
    connection.execute(
        """
        CREATE TABLE root_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)
        """
    )
    connection.execute(
        """
        CREATE TABLE interface_sessions (
            channel TEXT NOT NULL,
            session_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            PRIMARY KEY (channel, session_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE interface_session_messages (
            channel TEXT NOT NULL,
            session_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            PRIMARY KEY (channel, session_id, position)
        )
        """
    )
    connection.execute(
        "INSERT INTO root_metadata (key, value) VALUES (?, ?)",
        ("interfaces_schema_version", "1"),
    )
    connection.execute(
        "INSERT INTO interface_sessions (channel, session_id, revision) VALUES (?, ?, ?)",
        ("web", "", 1),
    )
    connection.execute(
        """
        INSERT INTO interface_session_messages (channel, session_id, position, role, content)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("web", "", 0, "user", "user text"),
    )
    connection.execute(
        """
        INSERT INTO interface_session_messages (channel, session_id, position, role, content)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("web", "", 1, "assistant", "Limited context: no user profile is available. reply"),
    )
    connection.commit()
    connection.close()

    reopened = SQLiteInterfaceRepository(legacy_path)
    turns = reopened.list_turns("web")
    assert len(turns) == 1
    assert turns[0].user_speech == "user text"
    assert turns[0].assistant_speech == "reply"
    assert turns[0].notices == (
        "Limited context: no user profile is available.",
    )
    messages = reopened.load().sessions[0].messages
    assert messages[0] == ChannelMessage("user", "user text")
    assert messages[1] == ChannelMessage("assistant", "reply")
