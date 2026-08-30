from __future__ import annotations

from datetime import datetime, timezone

from reckoning.planning import (
    CapacitySnapshot,
    GoalPlanningService,
    InMemoryPlanningRepository,
)


def test_direction_and_goal_lifecycles_keep_history_and_goal_contract() -> None:
    instant = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    repository = InMemoryPlanningRepository()
    service = GoalPlanningService(repository)
    service.create_direction("direction-1", "Become a strong backend engineer.", instant)
    active_direction = service.transition_direction("direction-1", "active", instant)
    goal = service.create_goal(
        goal_id="goal-1",
        direction_id="direction-1",
        target="Ship a tested API project.",
        success_evidence=("A reviewer can run the documented acceptance journey.",),
        constraints=("Ten hours per week",),
        dependencies=("goal-foundation",),
        deadline=None,
        importance=5,
        difficulty=4,
        weekly_effort_hours=8,
        created_at=instant,
    )
    active_goal = service.transition_goal("goal-1", "active", instant)

    assert active_direction.status == "active"
    assert active_goal.status == "active"
    assert goal.success_evidence
    assert goal.constraints == ("Ten hours per week",)
    assert goal.dependencies == ("goal-foundation",)
    assert goal.deadline is None
    assert [item.status for item in service.direction_history("direction-1")] == [
        "exploring",
        "active",
    ]
    assert [item.status for item in service.goal_history("goal-1")] == [
        "proposed",
        "active",
    ]


def test_feasible_set_uses_capacity_without_a_fixed_goal_count_and_explains_displacement() -> None:
    instant = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    repository = InMemoryPlanningRepository()
    service = GoalPlanningService(repository)
    service.create_direction("direction-1", "Build technical evidence.", instant)

    small_goals = tuple(
        service.create_goal(
            goal_id=f"small-{index}",
            direction_id="direction-1",
            target=f"Complete small outcome {index}.",
            success_evidence=(f"Outcome {index} exists.",),
            constraints=(),
            dependencies=(),
            deadline=None,
            importance=5 - index // 3,
            difficulty=1,
            weekly_effort_hours=1,
            created_at=instant,
        )
        for index in range(6)
    )
    large_goal = service.create_goal(
        goal_id="large",
        direction_id="direction-1",
        target="Complete the large outcome.",
        success_evidence=("Large outcome exists.",),
        constraints=(),
        dependencies=(),
        deadline=None,
        importance=1,
        difficulty=5,
        weekly_effort_hours=8,
        created_at=instant,
    )

    result = service.calculate_feasible_set(
        (*small_goals, large_goal),
        CapacitySnapshot(
            available_hours=20,
            obligation_hours=5,
            current_activity_hours=5,
            prior_outcome_factor=0.8,
        ),
    )

    assert len(result.included) == 6
    assert [item.goal_id for item in result.displaced] == ["large"]
    assert result.capacity.forecast_hours == 8
    assert "would displace time" in result.recommendation
    assert result.requires_user_choice is True
