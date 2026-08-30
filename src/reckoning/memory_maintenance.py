from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from reckoning.personal_context import (
    PersonalContextRepository,
    PersonalContextVersion,
)


@dataclass(frozen=True)
class MemoryIndexEntry:
    record_id: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class MaintenanceResult:
    id: str
    created_at: datetime
    summary: str
    index: tuple[MemoryIndexEntry, ...]
    duplicate_groups: tuple[tuple[str, ...], ...]
    source_record_ids: tuple[str, ...]


@dataclass(frozen=True)
class ObservedSignal:
    id: str
    observed_at: datetime
    description: str
    source: str


@dataclass(frozen=True)
class PatternProposal:
    id: str
    status: str
    window_start: datetime
    window_end: datetime
    observation_ids: tuple[str, ...]
    hypothesis: str
    alternatives: tuple[str, ...]
    uncertainty: str
    decision_relevance: str


class MaintenanceRepository(Protocol):
    def save_result(self, result: MaintenanceResult) -> None: ...

    def save_pattern(self, proposal: PatternProposal) -> None: ...

    def get_pattern(self, proposal_id: str) -> PatternProposal: ...

    def remove_context_links(self, record_id: str) -> None: ...


class InMemoryMaintenanceRepository:
    def __init__(self) -> None:
        self.results: dict[str, MaintenanceResult] = {}
        self.patterns: dict[str, PatternProposal] = {}

    def save_result(self, result: MaintenanceResult) -> None:
        self.results[result.id] = result

    def save_pattern(self, proposal: PatternProposal) -> None:
        self.patterns[proposal.id] = proposal

    def get_pattern(self, proposal_id: str) -> PatternProposal:
        try:
            return self.patterns[proposal_id]
        except KeyError as error:
            raise KeyError(f"Unknown pattern proposal: {proposal_id}") from error

    def remove_context_links(self, record_id: str) -> None:
        self.results = {
            result_id: MaintenanceResult(
                id=result.id,
                created_at=result.created_at,
                summary=result.summary,
                index=tuple(
                    item for item in result.index if item.record_id != record_id
                ),
                duplicate_groups=tuple(
                    remaining
                    for group in result.duplicate_groups
                    if (remaining := tuple(item for item in group if item != record_id))
                ),
                source_record_ids=tuple(
                    item for item in result.source_record_ids if item != record_id
                ),
            )
            for result_id, result in self.results.items()
        }


class MemoryMaintenanceService:
    def __init__(
        self,
        context: PersonalContextRepository,
        maintenance: MaintenanceRepository,
    ) -> None:
        self._context = context
        self._maintenance = maintenance

    def run(
        self,
        result_id: str,
        created_at: datetime,
        summarizer: Callable[[tuple[PersonalContextVersion, ...]], str],
    ) -> MaintenanceResult:
        sources = self._current_sources()
        summary = summarizer(sources).strip()
        if not summary:
            raise RuntimeError("Maintenance returned an empty summary.")

        groups: dict[str, list[str]] = {}
        for item in sources:
            groups.setdefault(item.canonical_meaning.casefold(), []).append(item.record_id)
        duplicates = tuple(
            tuple(record_ids)
            for record_ids in groups.values()
            if len(record_ids) > 1
        )
        result = MaintenanceResult(
            id=result_id,
            created_at=created_at,
            summary=summary,
            index=tuple(
                MemoryIndexEntry(
                    item.record_id,
                    tuple(sorted(set(item.canonical_meaning.casefold().split()))),
                )
                for item in sources
            ),
            duplicate_groups=duplicates,
            source_record_ids=tuple(item.record_id for item in sources),
        )
        self._maintenance.save_result(result)
        return result

    def propose_pattern(
        self,
        *,
        proposal_id: str,
        observations: tuple[ObservedSignal, ...],
        window_start: datetime,
        window_end: datetime,
        hypothesis: str,
        alternatives: tuple[str, ...],
        uncertainty: str,
        decision_relevance: str,
    ) -> PatternProposal:
        in_window = tuple(
            item
            for item in observations
            if window_start <= item.observed_at <= window_end
        )
        if len(in_window) < 2:
            raise ValueError("A pattern proposal requires multiple observations.")
        if not alternatives or not uncertainty.strip() or not decision_relevance.strip():
            raise ValueError(
                "A pattern proposal requires alternatives, uncertainty, and decision relevance."
            )
        proposal = PatternProposal(
            id=proposal_id,
            status="proposed",
            window_start=window_start,
            window_end=window_end,
            observation_ids=tuple(item.id for item in in_window),
            hypothesis=hypothesis.strip(),
            alternatives=alternatives,
            uncertainty=uncertainty.strip(),
            decision_relevance=decision_relevance.strip(),
        )
        self._maintenance.save_pattern(proposal)
        return proposal

    def review_pattern(self, proposal_id: str) -> PatternProposal:
        return self._maintenance.get_pattern(proposal_id)

    def confirm_pattern(self, proposal_id: str) -> None:
        self.review_pattern(proposal_id)
        raise PermissionError(
            "Maintenance cannot confirm a consequential inference. The user must "
            "confirm context through the personal-context path."
        )

    def may_consolidate(self, source_record_ids: tuple[str, ...]) -> bool:
        suppressed = {
            marker.deleted_record_id
            for marker in self._context.suppression_markers()
        }
        return not bool(suppressed & set(source_record_ids))

    def _current_sources(self) -> tuple[PersonalContextVersion, ...]:
        current: dict[str, PersonalContextVersion] = {}
        for item in self._context.all_versions():
            current[item.record_id] = item
        return tuple(current.values())
