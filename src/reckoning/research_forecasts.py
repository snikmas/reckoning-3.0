from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal, Protocol

SourceType = Literal["primary", "secondary"]
Sensitivity = Literal["public", "private", "restricted"]
EvidenceKind = Literal[
    "direct_information",
    "approved_assessment",
    "confirmed_obligation",
    "available_time",
    "observed_outcome",
    "personality_label",
]
MaterialChangeKind = Literal[
    "new_goal",
    "changed_capacity",
    "changed_deadline",
    "changed_official_requirement",
    "pace_outside_range",
]
ProposalStatus = Literal["proposed", "deferred", "rejected", "accepted"]


@dataclass(frozen=True)
class ResearchRequest:
    id: str
    question: str
    scope: str
    requested_at: datetime
    max_cost_units: int
    requires_current_primary_source: bool


@dataclass(frozen=True)
class ResearchSource:
    id: str
    title: str
    locator: str
    owner: str
    source_type: SourceType
    trust: str
    checked_at: datetime
    published_at: datetime | None
    scope: str
    sensitivity: Sensitivity
    cost_units: int


@dataclass(frozen=True)
class ResearchConclusion:
    request_id: str
    conclusion: str
    completed_at: datetime
    sources: tuple[ResearchSource, ...]
    total_cost_units: int
    created_watch: None = None
    created_routine: None = None
    granted_broader_permission: bool = False


@dataclass(frozen=True)
class ForecastEvidence:
    id: str
    kind: EvidenceKind
    description: str
    source: str
    observed_at: datetime


@dataclass(frozen=True)
class OfficialRequirementCheck:
    source_id: str
    owner: str
    locator: str
    requirement: str
    checked_at: datetime


@dataclass(frozen=True)
class GoalForecast:
    id: str
    goal_id: str
    version: int
    created_at: datetime
    target: str
    baseline: str
    effort_range_hours: tuple[float, float]
    money_range: tuple[float, float]
    resources: tuple[str, ...]
    target_start: datetime
    completion_range: tuple[datetime, datetime]
    confidence: str
    uncertainty: tuple[str, ...]
    evidence: tuple[ForecastEvidence, ...]
    assumptions: tuple[str, ...]
    competing_goal_ids: tuple[str, ...]
    replanning_triggers: tuple[str, ...]
    requirement_checks: tuple[OfficialRequirementCheck, ...]
    supersedes_forecast_id: str | None = None


@dataclass(frozen=True)
class MaterialChange:
    kind: MaterialChangeKind
    changed_at: datetime
    evidence: str


@dataclass(frozen=True)
class RevisionEffects:
    time: str
    cost: str
    resources: str
    displaced_goal_ids: tuple[str, ...]


@dataclass(frozen=True)
class ForecastRevisionProposal:
    id: str
    current_forecast_id: str
    proposed_forecast: GoalForecast
    material_change: MaterialChange
    effects: RevisionEffects
    explanation: str
    status: ProposalStatus = "proposed"


class ResearchForecastRepository(Protocol):
    def save_research(self, conclusion: ResearchConclusion) -> None: ...

    def get_research(self, request_id: str) -> ResearchConclusion: ...

    def save_forecast(self, forecast: GoalForecast, *, make_active: bool) -> None: ...

    def get_forecast(self, forecast_id: str) -> GoalForecast: ...

    def active_forecast(self, goal_id: str) -> GoalForecast: ...

    def save_proposal(self, proposal: ForecastRevisionProposal) -> None: ...

    def get_proposal(self, proposal_id: str) -> ForecastRevisionProposal: ...


class InMemoryResearchForecastRepository:
    def __init__(self) -> None:
        self.research: dict[str, ResearchConclusion] = {}
        self.forecasts: dict[str, GoalForecast] = {}
        self.active_forecasts: dict[str, str] = {}
        self.proposals: dict[str, ForecastRevisionProposal] = {}

    def save_research(self, conclusion: ResearchConclusion) -> None:
        self.research[conclusion.request_id] = conclusion

    def get_research(self, request_id: str) -> ResearchConclusion:
        try:
            return self.research[request_id]
        except KeyError as error:
            raise KeyError(f"Unknown research request: {request_id}") from error

    def save_forecast(self, forecast: GoalForecast, *, make_active: bool) -> None:
        self.forecasts[forecast.id] = forecast
        if make_active:
            self.active_forecasts[forecast.goal_id] = forecast.id

    def get_forecast(self, forecast_id: str) -> GoalForecast:
        try:
            return self.forecasts[forecast_id]
        except KeyError as error:
            raise KeyError(f"Unknown goal forecast: {forecast_id}") from error

    def active_forecast(self, goal_id: str) -> GoalForecast:
        try:
            return self.forecasts[self.active_forecasts[goal_id]]
        except KeyError as error:
            raise KeyError(f"No active forecast for goal: {goal_id}") from error

    def save_proposal(self, proposal: ForecastRevisionProposal) -> None:
        self.proposals[proposal.id] = proposal

    def get_proposal(self, proposal_id: str) -> ForecastRevisionProposal:
        try:
            return self.proposals[proposal_id]
        except KeyError as error:
            raise KeyError(f"Unknown forecast revision: {proposal_id}") from error


class BoundedResearchService:
    def __init__(self, repository: ResearchForecastRepository) -> None:
        self._repository = repository

    def complete(
        self,
        request: ResearchRequest,
        *,
        conclusion: str,
        sources: tuple[ResearchSource, ...],
        completed_at: datetime,
    ) -> ResearchConclusion:
        if not conclusion.strip():
            raise ValueError("A research conclusion cannot be empty.")
        if not sources:
            raise ValueError("A research conclusion requires at least one source.")
        if any(source.scope != request.scope for source in sources):
            raise PermissionError("A source falls outside the research request scope.")
        total_cost = sum(source.cost_units for source in sources)
        if total_cost > request.max_cost_units:
            raise PermissionError("The research request exceeded its cost limit.")
        if request.requires_current_primary_source and not any(
            source.source_type == "primary"
            and source.checked_at >= completed_at - timedelta(days=30)
            for source in sources
        ):
            raise ValueError("This decision requires a current primary source.")
        result = ResearchConclusion(
            request_id=request.id,
            conclusion=conclusion.strip(),
            completed_at=completed_at,
            sources=tuple(
                sorted(
                    sources,
                    key=lambda source: (
                        source.source_type != "primary",
                        -source.checked_at.timestamp(),
                    ),
                )
            ),
            total_cost_units=total_cost,
        )
        self._repository.save_research(result)
        return result

    def inspect(self, request_id: str) -> ResearchConclusion:
        return self._repository.get_research(request_id)

    def official_requirement_checks(
        self, request_id: str, requirement: str
    ) -> tuple[OfficialRequirementCheck, ...]:
        conclusion = self.inspect(request_id)
        checks = tuple(
            OfficialRequirementCheck(
                source_id=source.id,
                owner=source.owner,
                locator=source.locator,
                requirement=_required(requirement, "Official requirement"),
                checked_at=source.checked_at,
            )
            for source in conclusion.sources
            if source.source_type == "primary"
        )
        if not checks:
            raise ValueError("The research result has no primary official source.")
        return checks


class ForecastService:
    def __init__(self, repository: ResearchForecastRepository) -> None:
        self._repository = repository

    def create_confirmed(
        self,
        *,
        forecast_id: str,
        goal_id: str,
        created_at: datetime,
        target: str,
        baseline: str,
        effort_range_hours: tuple[float, float],
        money_range: tuple[float, float],
        resources: tuple[str, ...],
        target_start: datetime,
        completion_range: tuple[datetime, datetime],
        confidence: str,
        uncertainty: tuple[str, ...],
        evidence: tuple[ForecastEvidence, ...],
        assumptions: tuple[str, ...],
        competing_goal_ids: tuple[str, ...],
        replanning_triggers: tuple[str, ...],
        requirement_checks: tuple[OfficialRequirementCheck, ...],
    ) -> GoalForecast:
        self._validate_forecast_inputs(
            effort_range_hours,
            money_range,
            completion_range,
            evidence,
            requirement_checks,
        )
        forecast = GoalForecast(
            id=forecast_id,
            goal_id=goal_id,
            version=1,
            created_at=created_at,
            target=_required(target, "Forecast target"),
            baseline=_required(baseline, "Forecast baseline"),
            effort_range_hours=effort_range_hours,
            money_range=money_range,
            resources=resources,
            target_start=target_start,
            completion_range=completion_range,
            confidence=_required(confidence, "Confidence explanation"),
            uncertainty=uncertainty,
            evidence=evidence,
            assumptions=assumptions,
            competing_goal_ids=competing_goal_ids,
            replanning_triggers=replanning_triggers,
            requirement_checks=requirement_checks,
        )
        self._repository.save_forecast(forecast, make_active=True)
        return forecast

    def inspect(self, forecast_id: str) -> GoalForecast:
        return self._repository.get_forecast(forecast_id)

    def current(self, goal_id: str) -> GoalForecast:
        return self._repository.active_forecast(goal_id)

    def propose_revision(
        self,
        *,
        proposal_id: str,
        proposed_forecast_id: str,
        goal_id: str,
        created_at: datetime,
        material_change: MaterialChange,
        effects: RevisionEffects,
        explanation: str,
        effort_range_hours: tuple[float, float] | None = None,
        money_range: tuple[float, float] | None = None,
        resources: tuple[str, ...] | None = None,
        completion_range: tuple[datetime, datetime] | None = None,
        evidence: tuple[ForecastEvidence, ...] | None = None,
        requirement_checks: tuple[OfficialRequirementCheck, ...] | None = None,
        competing_goal_ids: tuple[str, ...] | None = None,
    ) -> ForecastRevisionProposal:
        current = self.current(goal_id)
        proposed = replace(
            current,
            id=proposed_forecast_id,
            version=current.version + 1,
            created_at=created_at,
            effort_range_hours=effort_range_hours or current.effort_range_hours,
            money_range=money_range or current.money_range,
            resources=resources or current.resources,
            completion_range=completion_range or current.completion_range,
            evidence=evidence or current.evidence,
            requirement_checks=requirement_checks or current.requirement_checks,
            competing_goal_ids=(
                competing_goal_ids
                if competing_goal_ids is not None
                else current.competing_goal_ids
            ),
            supersedes_forecast_id=current.id,
        )
        self._validate_forecast_inputs(
            proposed.effort_range_hours,
            proposed.money_range,
            proposed.completion_range,
            proposed.evidence,
            proposed.requirement_checks,
        )
        if not all((effects.time.strip(), effects.cost.strip(), effects.resources.strip())):
            raise ValueError("Revision effects must explain time, cost, and resources.")
        proposal = ForecastRevisionProposal(
            id=proposal_id,
            current_forecast_id=current.id,
            proposed_forecast=proposed,
            material_change=material_change,
            effects=effects,
            explanation=_required(explanation, "Revision explanation"),
        )
        self._repository.save_forecast(proposed, make_active=False)
        self._repository.save_proposal(proposal)
        return proposal

    def defer_revision(self, proposal_id: str) -> ForecastRevisionProposal:
        return self._set_proposal_status(proposal_id, "deferred")

    def reject_revision(self, proposal_id: str) -> ForecastRevisionProposal:
        return self._set_proposal_status(proposal_id, "rejected")

    def accept_revision(self, proposal_id: str) -> ForecastRevisionProposal:
        proposal = self._repository.get_proposal(proposal_id)
        if proposal.status not in ("proposed", "deferred"):
            raise ValueError(f"Cannot accept a {proposal.status} forecast revision.")
        accepted = replace(proposal, status="accepted")
        self._repository.save_forecast(
            accepted.proposed_forecast, make_active=True
        )
        self._repository.save_proposal(accepted)
        return accepted

    def _set_proposal_status(
        self, proposal_id: str, status: ProposalStatus
    ) -> ForecastRevisionProposal:
        proposal = self._repository.get_proposal(proposal_id)
        if proposal.status == "accepted":
            raise ValueError("An accepted forecast revision cannot be changed.")
        changed = replace(proposal, status=status)
        self._repository.save_proposal(changed)
        return changed

    @staticmethod
    def _validate_forecast_inputs(
        effort_range: tuple[float, float],
        money_range: tuple[float, float],
        completion_range: tuple[datetime, datetime],
        evidence: tuple[ForecastEvidence, ...],
        requirement_checks: tuple[OfficialRequirementCheck, ...],
    ) -> None:
        if effort_range[0] < 0 or effort_range[0] > effort_range[1]:
            raise ValueError("The effort range is invalid.")
        if money_range[0] < 0 or money_range[0] > money_range[1]:
            raise ValueError("The money range is invalid.")
        if completion_range[0] > completion_range[1]:
            raise ValueError("The completion range is invalid.")
        if not evidence:
            raise ValueError("A forecast requires capacity evidence.")
        if any(item.kind == "personality_label" for item in evidence):
            raise ValueError("A personality label cannot act as capacity evidence.")
        if not requirement_checks:
            raise ValueError("A forecast must record current official requirement checks.")
        if any(
            not all(
                (
                    item.source_id.strip(),
                    item.owner.strip(),
                    item.locator.strip(),
                    item.requirement.strip(),
                )
            )
            for item in requirement_checks
        ):
            raise ValueError("An official requirement check is incomplete.")


def _required(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} cannot be empty.")
    return cleaned
