from __future__ import annotations

from datetime import datetime, timedelta, timezone

from reckoning.planning import InMemoryPlanningRepository, PlanService


def test_horizons_have_separate_purposes_and_used_plan_changes_create_revisions() -> None:
    instant = datetime(2026, 8, 30, 21, 0, tzinfo=timezone.utc)
    repository = InMemoryPlanningRepository()
    service = PlanService(repository)
    plans = tuple(
        service.create(
            plan_id=f"{horizon}-plan",
            horizon=horizon,
            cadence=cadence,
            outcomes=(f"One {horizon} outcome",),
            effective_at=instant,
        )
        for horizon, cadence in (
            ("horizon", "monthly"),
            ("weekly", "every Sunday"),
            ("day", "each morning"),
        )
    )

    assert len({plan.purpose for plan in plans}) == 3
    assert [plan.cadence for plan in plans] == [
        "monthly",
        "every Sunday",
        "each morning",
    ]

    used = service.mark_used("weekly-plan", instant + timedelta(days=1))
    revised = service.revise(
        "weekly-plan",
        outcomes=("A smaller weekly outcome",),
        effective_at=instant + timedelta(days=2),
    )

    assert used.status == "used"
    assert used.outcomes == ("One weekly outcome",)
    assert revised.version == 3
    assert revised.supersedes_version == 2
    assert service.history("weekly-plan")[1] == used


def test_missed_commitment_records_learning_without_judgment_or_rollover() -> None:
    instant = datetime(2026, 8, 30, 21, 0, tzinfo=timezone.utc)
    repository = InMemoryPlanningRepository()
    service = PlanService(repository)
    plan = service.create(
        plan_id="weekly-plan",
        horizon="weekly",
        cadence="every Sunday",
        outcomes=("Finish the API acceptance test.",),
        effective_at=instant,
    )
    used = service.mark_used(plan.plan_id, instant)

    check_in = service.check_in(
        check_in_id="check-in-1",
        plan_id=used.plan_id,
        plan_version=used.version,
        recorded_at=instant + timedelta(days=7),
        intended_outcome="Finish the API acceptance test.",
        actual_outcome="The provider contract changed and only the adapter was finished.",
        outcome="missed",
        estimate_learning="Allow one extra block for provider contract verification.",
        blockers=("Provider contract changed",),
        priority_effect="Keep the outcome only if it still outranks the exam deadline.",
    )

    assert check_in.outcome == "missed"
    assert check_in.estimate_learning.startswith("Allow one extra block")
    assert check_in.blockers == ("Provider contract changed",)
    assert check_in.character_judgment is None
    assert check_in.automatically_rolled_over is False
    assert service.history("weekly-plan")[-1] == used
