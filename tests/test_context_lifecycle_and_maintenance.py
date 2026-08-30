from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from reckoning.memory_maintenance import (
    InMemoryMaintenanceRepository,
    MemoryMaintenanceService,
    ObservedSignal,
)
from reckoning.personal_context import (
    JsonFilePersonalContextRepository,
    PersonalContextService,
    RetrievalQuery,
)


def build_context(
    path: Path, instant: datetime
) -> tuple[JsonFilePersonalContextRepository, PersonalContextService]:
    repository = JsonFilePersonalContextRepository(path)
    service = PersonalContextService(repository)
    service.remember(
        record_id="fact-1",
        original_text="I study Chinese on Wednesday.",
        language="en",
        canonical_meaning="Mary studies Chinese on Wednesday.",
        source="direct user statement",
        created_at=instant,
    )
    return repository, service


def query(instant: datetime) -> RetrievalQuery:
    return RetrievalQuery(
        "Chinese Wednesday",
        instant,
        ("direct user statement",),
        ("low",),
        "local",
    )


def test_archive_forget_recover_and_permanent_delete_have_distinct_behavior(
    tmp_path: Path,
) -> None:
    instant = datetime(2026, 8, 30, 19, 0, tzinfo=timezone.utc)
    path = tmp_path / "context.json"
    repository, service = build_context(path, instant)

    service.archive("fact-1", instant + timedelta(minutes=1))
    assert service.list_active() == ()
    assert service.inspect("fact-1").current.status == "archived"

    service.forget("fact-1", instant + timedelta(minutes=2))
    assert service.retrieve(query(instant + timedelta(minutes=3))) == ()
    assert service.inspect("fact-1").current.status == "forgotten"

    service.recover("fact-1", instant + timedelta(minutes=4))
    assert service.retrieve(query(instant + timedelta(minutes=5)))[0].status == "active"

    marker = service.delete("fact-1", instant + timedelta(minutes=6))
    assert repository.all_versions() == ()
    assert marker.deleted_record_id == "fact-1"
    assert marker.__dict__.keys() == {"deleted_record_id", "deleted_at"}

    restarted_repository = JsonFilePersonalContextRepository(path)
    assert restarted_repository.all_versions() == ()
    assert restarted_repository.suppression_markers() == (marker,)


def test_maintenance_is_atomic_proposes_reviewable_patterns_and_respects_deletion(
    tmp_path: Path,
) -> None:
    instant = datetime(2026, 8, 1, tzinfo=timezone.utc)
    context_repository, _ = build_context(tmp_path / "context.json", instant)
    maintenance_repository = InMemoryMaintenanceRepository()
    context = PersonalContextService(
        context_repository, link_removers=(maintenance_repository,)
    )
    maintenance = MemoryMaintenanceService(
        context_repository, maintenance_repository
    )

    def fail_after_reading(sources: object) -> str:
        raise RuntimeError("summarizer failed")

    with pytest.raises(RuntimeError, match="summarizer failed"):
        maintenance.run("run-failed", instant, fail_after_reading)

    assert len(context_repository.all_versions()) == 1
    assert maintenance_repository.results == {}

    result = maintenance.run(
        "run-1",
        instant,
        lambda sources: "Mary studies Chinese on Wednesday.",
    )
    assert result.source_record_ids == ("fact-1",)
    assert result.index[0].record_id == "fact-1"

    observations = (
        ObservedSignal("signal-1", instant, "Started after lunch", "plan outcome"),
        ObservedSignal(
            "signal-2",
            instant + timedelta(days=14),
            "Started after lunch again",
            "plan outcome",
        ),
    )
    proposal = maintenance.propose_pattern(
        proposal_id="pattern-1",
        observations=observations,
        window_start=instant,
        window_end=instant + timedelta(days=30),
        hypothesis="Afternoon starts may be easier to sustain.",
        alternatives=("The task type, not the time, may explain both starts.",),
        uncertainty="Two observations are weak evidence.",
        decision_relevance="Test one afternoon study block next week.",
    )
    assert proposal.status == "proposed"
    assert proposal.observation_ids == ("signal-1", "signal-2")
    assert maintenance.review_pattern("pattern-1") == proposal
    with pytest.raises(PermissionError, match="cannot confirm"):
        maintenance.confirm_pattern("pattern-1")

    context.delete("fact-1", instant + timedelta(days=31))
    assert maintenance_repository.results["run-1"].source_record_ids == ()
    assert maintenance_repository.results["run-1"].index == ()
    assert maintenance.may_consolidate(("fact-1",)) is False
