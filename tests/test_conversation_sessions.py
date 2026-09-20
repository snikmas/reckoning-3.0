"""Conversation session lifecycle, selection, and concurrency."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from reckoning.interfaces import (
    ChannelSession,
    InterfaceSessionConflict,
    JsonFileInterfaceRepository,
)


def test_create_session_returns_stable_identifier_and_metadata(tmp_path: Path) -> None:
    repository = JsonFileInterfaceRepository(tmp_path / "interfaces.json")
    created = repository.create_session(
        "web", display_name="Planning", origin="user-created", created_at=datetime.now(timezone.utc)
    )

    assert created.session_id
    assert created.display_name == "Planning"
    assert created.origin == "user-created"
    assert created.channel == "web"
    assert created.revision == 1


def test_list_sessions_filters_by_channel(tmp_path: Path) -> None:
    repository = JsonFileInterfaceRepository(tmp_path / "interfaces.json")
    web = repository.create_session("web", None, "user-created", datetime.now(timezone.utc))
    terminal = repository.create_session(
        "terminal", None, "user-created", datetime.now(timezone.utc)
    )

    assert repository.list_sessions("web") == (web,)
    assert repository.list_sessions("terminal") == (terminal,)


def test_selection_requires_expected_revision(tmp_path: Path) -> None:
    repository = JsonFileInterfaceRepository(tmp_path / "interfaces.json")
    session = repository.create_session("web", None, "user-created", datetime.now(timezone.utc))

    repository.select_session(
        "web", session.session_id, expected_selection_revision=0
    )
    with pytest.raises(InterfaceSessionConflict):
        repository.select_session(
            "web", session.session_id, expected_selection_revision=0
        )


def test_get_selected_session_returns_current_selection(tmp_path: Path) -> None:
    repository = JsonFileInterfaceRepository(tmp_path / "interfaces.json")
    session = repository.create_session("web", None, "user-created", datetime.now(timezone.utc))

    repository.select_session(
        "web", session.session_id, expected_selection_revision=0
    )
    selection = repository.get_selected_session("web")
    assert selection is not None
    assert selection.selected_session_id == session.session_id
    assert selection.revision == 1


def test_start_first_session_is_idempotent(tmp_path: Path) -> None:
    repository = JsonFileInterfaceRepository(tmp_path / "interfaces.json")
    first = repository.start_first_session("web")
    second = repository.start_first_session("web")

    assert first.session_id == second.session_id
    assert repository.list_sessions("web") == (first,)


def test_concurrent_append_to_same_session_conflicts(tmp_path: Path) -> None:
    repository = JsonFileInterfaceRepository(tmp_path / "interfaces.json")
    session = repository.create_session("web", None, "user-created", datetime.now(timezone.utc))

    def append() -> None:
        repository.append_completed_turn(
            "web",
            session.session_id,
            " concurrent",
            "reply",
            expected_revision=session.revision,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(append)
        second = executor.submit(append)
    results = [first.exception(), second.exception()]
    conflicts = [r for r in results if isinstance(r, InterfaceSessionConflict)]
    successes = [r for r in results if r is None]

    assert len(conflicts) == 1
    assert len(successes) == 1


def test_concurrent_append_to_different_sessions_succeed(tmp_path: Path) -> None:
    repository = JsonFileInterfaceRepository(tmp_path / "interfaces.json")
    one = repository.create_session("web", None, "user-created", datetime.now(timezone.utc))
    two = repository.create_session("web", None, "user-created", datetime.now(timezone.utc))

    def append(session: ChannelSession) -> None:
        repository.append_completed_turn(
            "web",
            session.session_id,
            "hello",
            "reply",
            expected_revision=session.revision,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(append, one)
        second = executor.submit(append, two)
    assert first.exception() is None
    assert second.exception() is None


def test_legacy_empty_session_migrates_to_labeled_legacy_session(tmp_path: Path) -> None:
    import json

    path = tmp_path / "interfaces.json"
    legacy = {
        "schema_version": 1,
        "state": {
            "returning_user": True,
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
    assert len(state.sessions) == 1
    session = state.sessions[0]
    assert session.session_id == "legacy-web"
    assert session.origin == "legacy"
    assert session.display_name == "Legacy web"
    assert session.messages[0].content == "Legacy question"

    selection = repository.get_selected_session("web")
    assert selection is not None
    assert selection.selected_session_id == "legacy-web"
