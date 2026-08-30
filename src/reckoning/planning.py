from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, Protocol

DirectionStatus = Literal["exploring", "active", "paused", "completed"]
GoalStatus = Literal["proposed", "active", "paused", "completed", "abandoned"]
PlanHorizon = Literal["horizon", "weekly", "day"]
PlanStatus = Literal["draft", "used"]
CheckInOutcome = Literal["completed", "partial", "missed"]


@dataclass(frozen=True)
class DirectionVersion:
    direction_id: str
    version: int
    status: DirectionStatus
    statement: str
    created_at: datetime
    supersedes_version: int | None = None


@dataclass(frozen=True)
class GoalVersion:
    goal_id: str
    version: int
    status: GoalStatus
    direction_id: str
    target: str
    success_evidence: tuple[str, ...]
    constraints: tuple[str, ...]
    dependencies: tuple[str, ...]
    deadline: datetime | None
    importance: int
    difficulty: int
    weekly_effort_hours: float
    created_at: datetime
    supersedes_version: int | None = None


@dataclass(frozen=True)
class CapacitySnapshot:
    available_hours: float
    obligation_hours: float
    current_activity_hours: float
    prior_outcome_factor: float

    @property
    def forecast_hours(self) -> float:
        raw = self.available_hours - self.obligation_hours - self.current_activity_hours
        return max(0.0, raw * min(1.25, max(0.25, self.prior_outcome_factor)))


@dataclass(frozen=True)
class GoalFit:
    goal_id: str
    adjusted_effort_hours: float
    reason: str


@dataclass(frozen=True)
class FeasibleGoalSet:
    capacity: CapacitySnapshot
    included: tuple[GoalFit, ...]
    displaced: tuple[GoalFit, ...]
    recommendation: str
    requires_user_choice: bool = True


@dataclass(frozen=True)
class PlanVersion:
    plan_id: str
    version: int
    horizon: PlanHorizon
    purpose: str
    cadence: str
    outcomes: tuple[str, ...]
    effective_at: datetime
    status: PlanStatus
    supersedes_version: int | None = None


@dataclass(frozen=True)
class PlanCheckIn:
    id: str
    plan_id: str
    plan_version: int
    recorded_at: datetime
    intended_outcome: str
    actual_outcome: str
    outcome: CheckInOutcome
    estimate_learning: str
    blockers: tuple[str, ...]
    priority_effect: str
    character_judgment: None = None
    automatically_rolled_over: bool = False


class PlanningRepository(Protocol):
    def save_direction(self, direction: DirectionVersion) -> None: ...

    def direction_versions(self, direction_id: str) -> tuple[DirectionVersion, ...]: ...

    def save_goal(self, goal: GoalVersion) -> None: ...

    def goal_versions(self, goal_id: str) -> tuple[GoalVersion, ...]: ...

    def save_plan(self, plan: PlanVersion) -> None: ...

    def plan_versions(self, plan_id: str) -> tuple[PlanVersion, ...]: ...

    def save_check_in(self, check_in: PlanCheckIn) -> None: ...

    def plan_check_ins(self, plan_id: str) -> tuple[PlanCheckIn, ...]: ...


class InMemoryPlanningRepository:
    def __init__(self) -> None:
        self.directions: list[DirectionVersion] = []
        self.goals: list[GoalVersion] = []
        self.plans: list[PlanVersion] = []
        self.check_ins: list[PlanCheckIn] = []

    def save_direction(self, direction: DirectionVersion) -> None:
        self.directions.append(direction)

    def direction_versions(self, direction_id: str) -> tuple[DirectionVersion, ...]:
        return tuple(item for item in self.directions if item.direction_id == direction_id)

    def save_goal(self, goal: GoalVersion) -> None:
        self.goals.append(goal)

    def goal_versions(self, goal_id: str) -> tuple[GoalVersion, ...]:
        return tuple(item for item in self.goals if item.goal_id == goal_id)

    def save_plan(self, plan: PlanVersion) -> None:
        self.plans.append(plan)

    def plan_versions(self, plan_id: str) -> tuple[PlanVersion, ...]:
        return tuple(item for item in self.plans if item.plan_id == plan_id)

    def save_check_in(self, check_in: PlanCheckIn) -> None:
        self.check_ins.append(check_in)

    def plan_check_ins(self, plan_id: str) -> tuple[PlanCheckIn, ...]:
        return tuple(item for item in self.check_ins if item.plan_id == plan_id)


class GoalPlanningService:
    def __init__(self, repository: PlanningRepository) -> None:
        self._repository = repository

    def create_direction(
        self, direction_id: str, statement: str, created_at: datetime
    ) -> DirectionVersion:
        direction = DirectionVersion(
            direction_id,
            1,
            "exploring",
            _required(statement, "Direction"),
            created_at,
        )
        self._repository.save_direction(direction)
        return direction

    def transition_direction(
        self, direction_id: str, status: DirectionStatus, changed_at: datetime
    ) -> DirectionVersion:
        current = self._current_direction(direction_id)
        changed = replace(
            current,
            version=current.version + 1,
            status=status,
            created_at=changed_at,
            supersedes_version=current.version,
        )
        self._repository.save_direction(changed)
        return changed

    def create_goal(
        self,
        *,
        goal_id: str,
        direction_id: str,
        target: str,
        success_evidence: tuple[str, ...],
        constraints: tuple[str, ...],
        dependencies: tuple[str, ...],
        deadline: datetime | None,
        importance: int,
        difficulty: int,
        weekly_effort_hours: float,
        created_at: datetime,
    ) -> GoalVersion:
        self._current_direction(direction_id)
        if not success_evidence:
            raise ValueError("A goal must define success evidence.")
        if not 1 <= importance <= 5 or not 1 <= difficulty <= 5:
            raise ValueError("Importance and difficulty must be between 1 and 5.")
        if weekly_effort_hours <= 0:
            raise ValueError("Weekly effort must be positive.")
        goal = GoalVersion(
            goal_id=goal_id,
            version=1,
            status="proposed",
            direction_id=direction_id,
            target=_required(target, "Goal target"),
            success_evidence=success_evidence,
            constraints=constraints,
            dependencies=dependencies,
            deadline=deadline,
            importance=importance,
            difficulty=difficulty,
            weekly_effort_hours=weekly_effort_hours,
            created_at=created_at,
        )
        self._repository.save_goal(goal)
        return goal

    def transition_goal(
        self, goal_id: str, status: GoalStatus, changed_at: datetime
    ) -> GoalVersion:
        current = self._current_goal(goal_id)
        changed = replace(
            current,
            version=current.version + 1,
            status=status,
            created_at=changed_at,
            supersedes_version=current.version,
        )
        self._repository.save_goal(changed)
        return changed

    def direction_history(self, direction_id: str) -> tuple[DirectionVersion, ...]:
        return self._repository.direction_versions(direction_id)

    def goal_history(self, goal_id: str) -> tuple[GoalVersion, ...]:
        return self._repository.goal_versions(goal_id)

    def calculate_feasible_set(
        self,
        goals: tuple[GoalVersion, ...],
        capacity: CapacitySnapshot,
    ) -> FeasibleGoalSet:
        current_goals = {goal.goal_id: goal for goal in goals}
        ranked = sorted(
            (goal for goal in goals if goal.status in ("proposed", "active")),
            key=lambda item: (
                -item.importance,
                item.deadline or datetime.max.replace(tzinfo=item.created_at.tzinfo),
                item.difficulty,
            ),
        )
        remaining = capacity.forecast_hours
        included: list[GoalFit] = []
        displaced: list[GoalFit] = []

        for goal in ranked:
            adjusted_effort = round(
                goal.weekly_effort_hours * (0.75 + goal.difficulty * 0.1), 2
            )
            missing_dependencies = tuple(
                dependency
                for dependency in goal.dependencies
                if dependency not in current_goals
                or current_goals[dependency].status not in ("active", "completed")
            )
            if missing_dependencies:
                displaced.append(
                    GoalFit(
                        goal.goal_id,
                        adjusted_effort,
                        f"Blocked by dependencies: {', '.join(missing_dependencies)}.",
                    )
                )
            elif adjusted_effort <= remaining:
                included.append(
                    GoalFit(
                        goal.goal_id,
                        adjusted_effort,
                        f"Fits after importance {goal.importance}, difficulty "
                        f"{goal.difficulty}, and prior outcomes were applied.",
                    )
                )
                remaining -= adjusted_effort
            else:
                displaced.append(
                    GoalFit(
                        goal.goal_id,
                        adjusted_effort,
                        f"Needs {adjusted_effort:.2f} hours but only "
                        f"{remaining:.2f} forecast hours remain.",
                    )
                )

        if displaced and included:
            recommendation = (
                f"Keep {', '.join(item.goal_id for item in included)}. Adding "
                f"{displaced[0].goal_id} would displace time already assigned to "
                f"{included[-1].goal_id}; Mary must choose that trade-off."
            )
        elif displaced:
            recommendation = (
                "The current obligations and activity leave no supported fit. "
                "Reduce scope or free time before activating a goal."
            )
        else:
            recommendation = (
                "Every candidate fits the current evidence. Mary still decides "
                "which goals become active."
            )
        return FeasibleGoalSet(
            capacity=capacity,
            included=tuple(included),
            displaced=tuple(displaced),
            recommendation=recommendation,
        )

    def _current_direction(self, direction_id: str) -> DirectionVersion:
        versions = self._repository.direction_versions(direction_id)
        if not versions:
            raise KeyError(f"Unknown direction: {direction_id}")
        return versions[-1]

    def _current_goal(self, goal_id: str) -> GoalVersion:
        versions = self._repository.goal_versions(goal_id)
        if not versions:
            raise KeyError(f"Unknown goal: {goal_id}")
        return versions[-1]


class PlanService:
    PURPOSES: dict[PlanHorizon, str] = {
        "horizon": "Sequence goals and expose trade-offs over months or longer.",
        "weekly": "Choose a limited set of outcomes under known weekly capacity.",
        "day": "Allocate fixed commitments, meaningful work, buffers, and rest today.",
    }

    def __init__(self, repository: PlanningRepository) -> None:
        self._repository = repository

    def create(
        self,
        *,
        plan_id: str,
        horizon: PlanHorizon,
        cadence: str,
        outcomes: tuple[str, ...],
        effective_at: datetime,
    ) -> PlanVersion:
        if not outcomes:
            raise ValueError("A plan must contain at least one intended outcome.")
        plan = PlanVersion(
            plan_id=plan_id,
            version=1,
            horizon=horizon,
            purpose=self.PURPOSES[horizon],
            cadence=_required(cadence, "Cadence"),
            outcomes=outcomes,
            effective_at=effective_at,
            status="draft",
        )
        self._repository.save_plan(plan)
        return plan

    def mark_used(self, plan_id: str, used_at: datetime) -> PlanVersion:
        current = self._current(plan_id)
        if current.status == "used":
            return current
        used = replace(
            current,
            version=current.version + 1,
            status="used",
            effective_at=used_at,
            supersedes_version=current.version,
        )
        self._repository.save_plan(used)
        return used

    def revise(
        self,
        plan_id: str,
        *,
        outcomes: tuple[str, ...],
        effective_at: datetime,
        cadence: str | None = None,
    ) -> PlanVersion:
        current = self._current(plan_id)
        if not outcomes:
            raise ValueError("A plan revision must contain an intended outcome.")
        revision = replace(
            current,
            version=current.version + 1,
            status="draft",
            cadence=_required(cadence, "Cadence") if cadence else current.cadence,
            outcomes=outcomes,
            effective_at=effective_at,
            supersedes_version=current.version,
        )
        self._repository.save_plan(revision)
        return revision

    def check_in(
        self,
        *,
        check_in_id: str,
        plan_id: str,
        plan_version: int,
        recorded_at: datetime,
        intended_outcome: str,
        actual_outcome: str,
        outcome: CheckInOutcome,
        estimate_learning: str,
        blockers: tuple[str, ...],
        priority_effect: str,
    ) -> PlanCheckIn:
        versions = self._repository.plan_versions(plan_id)
        if not any(item.version == plan_version for item in versions):
            raise KeyError(f"Unknown plan version: {plan_id} v{plan_version}")
        record = PlanCheckIn(
            id=check_in_id,
            plan_id=plan_id,
            plan_version=plan_version,
            recorded_at=recorded_at,
            intended_outcome=_required(intended_outcome, "Intended outcome"),
            actual_outcome=_required(actual_outcome, "Actual outcome"),
            outcome=outcome,
            estimate_learning=_required(estimate_learning, "Estimate learning"),
            blockers=blockers,
            priority_effect=_required(priority_effect, "Priority effect"),
        )
        self._repository.save_check_in(record)
        return record

    def history(self, plan_id: str) -> tuple[PlanVersion, ...]:
        return self._repository.plan_versions(plan_id)

    def check_ins(self, plan_id: str) -> tuple[PlanCheckIn, ...]:
        return self._repository.plan_check_ins(plan_id)

    def _current(self, plan_id: str) -> PlanVersion:
        versions = self._repository.plan_versions(plan_id)
        if not versions:
            raise KeyError(f"Unknown plan: {plan_id}")
        return versions[-1]


def _required(value: str | None, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError(f"{label} cannot be empty.")
    return cleaned
