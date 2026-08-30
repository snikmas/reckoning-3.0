from __future__ import annotations

from datetime import datetime, timedelta, timezone

from reckoning.research_forecasts import (
    ForecastEvidence,
    ForecastService,
    InMemoryResearchForecastRepository,
    MaterialChange,
    RevisionEffects,
)
from test_goal_forecast import forecast_arguments


def test_material_change_proposes_revision_without_replacing_current_forecast() -> None:
    instant = datetime(2026, 8, 30, 23, 0, tzinfo=timezone.utc)
    repository = InMemoryResearchForecastRepository()
    service = ForecastService(repository)
    current = service.create_confirmed(**forecast_arguments(instant))
    changed_at = instant + timedelta(days=30)
    revised_evidence = current.evidence + (
        ForecastEvidence(
            "evidence-pace",
            "observed_outcome",
            "Observed pace was four hours per week instead of six.",
            "Four weekly check-ins",
            changed_at,
        ),
    )

    proposal = service.propose_revision(
        proposal_id="revision-1",
        proposed_forecast_id="forecast-2",
        goal_id=current.goal_id,
        created_at=changed_at,
        material_change=MaterialChange(
            "pace_outside_range",
            changed_at,
            "Four check-ins show a lower pace than the confirmed assumption.",
        ),
        effects=RevisionEffects(
            time="Completion moves roughly three months later.",
            cost="No additional mandatory spending.",
            resources="Keep feedback but reduce weekly test volume.",
            displaced_goal_ids=("goal-backend",),
        ),
        explanation="The observed pace invalidates the six-hour weekly assumption.",
        effort_range_hours=(300.0, 420.0),
        completion_range=(
            current.completion_range[0] + timedelta(days=90),
            current.completion_range[1] + timedelta(days=90),
        ),
        evidence=revised_evidence,
    )

    assert proposal.material_change.kind == "pace_outside_range"
    assert proposal.effects.displaced_goal_ids == ("goal-backend",)
    assert proposal.proposed_forecast.supersedes_forecast_id == current.id
    assert service.current(current.goal_id) == current

    deferred = service.defer_revision(proposal.id)
    assert deferred.status == "deferred"
    assert service.current(current.goal_id) == current

    rejected = service.reject_revision(proposal.id)
    assert rejected.status == "rejected"
    assert service.current(current.goal_id) == current


def test_accepting_revision_is_the_only_action_that_changes_current_forecast() -> None:
    instant = datetime(2026, 8, 30, 23, 0, tzinfo=timezone.utc)
    repository = InMemoryResearchForecastRepository()
    service = ForecastService(repository)
    current = service.create_confirmed(**forecast_arguments(instant))
    proposal = service.propose_revision(
        proposal_id="revision-1",
        proposed_forecast_id="forecast-2",
        goal_id=current.goal_id,
        created_at=instant + timedelta(days=1),
        material_change=MaterialChange(
            "changed_capacity",
            instant + timedelta(days=1),
            "Available time increased from six to eight hours.",
        ),
        effects=RevisionEffects(
            time="Completion may move earlier.",
            cost="No cost change.",
            resources="Add one practice block.",
            displaced_goal_ids=(),
        ),
        explanation="Confirmed capacity increased.",
        effort_range_hours=(220.0, 330.0),
    )

    accepted = service.accept_revision(proposal.id)

    assert accepted.status == "accepted"
    assert service.current(current.goal_id).id == "forecast-2"
    assert service.inspect(current.id) == current
