from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from reckoning.json_store import atomic_write_json, read_json

RoutineProposalStatus = Literal["proposed", "confirmed", "superseded"]
RoutineRunStatus = Literal["running", "success", "partial", "blocked", "failed"]
StepKind = Literal["deterministic", "model"]
StepStatus = Literal["success", "partial", "blocked", "failed"]


@dataclass(frozen=True)
class RoutineProposal:
    id: str
    routine_id: str
    version: int
    source_request: str
    created_at: datetime
    trigger: str
    source_scope: tuple[str, ...]
    context_scope: tuple[str, ...]
    tools: tuple[str, ...]
    permissions: tuple[str, ...]
    delivery: str
    model_policy: str
    cost_ceiling: int
    retry_limit: int
    delegation_policy: str
    failure_behavior: str
    status: RoutineProposalStatus = "proposed"
    supersedes_proposal_id: str | None = None


@dataclass(frozen=True)
class RoutineContractDraft:
    trigger: str
    source_scope: tuple[str, ...]
    context_scope: tuple[str, ...]
    tools: tuple[str, ...]
    permissions: tuple[str, ...]
    delivery: str
    model_policy: str
    cost_ceiling: int
    retry_limit: int
    delegation_policy: str
    failure_behavior: str


class RoutineCompiler(Protocol):
    def compile(self, request: str) -> RoutineContractDraft: ...


@dataclass(frozen=True)
class RoutineStep:
    id: str
    kind: StepKind
    action: str


@dataclass(frozen=True)
class StepResult:
    status: StepStatus
    detail: str
    cost_units: int = 0
    model_calls: int = 0


@dataclass(frozen=True)
class RoutineRun:
    id: str
    proposal_id: str
    scheduled_for: datetime
    idempotency_key: str
    steps: tuple[RoutineStep, ...]
    status: RoutineRunStatus = "running"
    current_step: int = 0
    attempts: int = 0
    current_step_attempts: int = 0
    results: tuple[StepResult, ...] = ()


@dataclass(frozen=True)
class RoutineReceipt:
    run_id: str
    proposal_id: str
    status: Literal["success", "partial", "blocked", "failed"]
    completed_at: datetime
    attempts: int
    results: tuple[StepResult, ...]
    executed: bool = True


@dataclass(frozen=True)
class NoRunReceipt:
    proposal_id: str
    evaluated_at: datetime
    reason: str
    status: Literal["no_run"] = "no_run"
    executed: bool = False


class StepExecutor(Protocol):
    def execute(self, step: RoutineStep, idempotency_key: str) -> StepResult: ...


class AutomationRepository(Protocol):
    def save_proposal(self, proposal: RoutineProposal) -> None: ...

    def get_proposal(self, proposal_id: str) -> RoutineProposal: ...

    def proposals_for(self, routine_id: str) -> tuple[RoutineProposal, ...]: ...

    def list_proposals(self) -> tuple[RoutineProposal, ...]: ...

    def save_run(self, run: RoutineRun) -> None: ...

    def get_run(self, run_id: str) -> RoutineRun: ...

    def find_run_by_key(self, idempotency_key: str) -> RoutineRun | None: ...

    def save_receipt(self, receipt: RoutineReceipt) -> None: ...

    def get_receipt(self, run_id: str) -> RoutineReceipt | None: ...


class InMemoryAutomationRepository:
    def __init__(self) -> None:
        self.proposals: dict[str, RoutineProposal] = {}
        self.runs: dict[str, RoutineRun] = {}
        self.receipts: dict[str, RoutineReceipt] = {}

    def save_proposal(self, proposal: RoutineProposal) -> None:
        self.proposals[proposal.id] = proposal

    def get_proposal(self, proposal_id: str) -> RoutineProposal:
        try:
            return self.proposals[proposal_id]
        except KeyError as error:
            raise KeyError(f"Unknown routine proposal: {proposal_id}") from error

    def proposals_for(self, routine_id: str) -> tuple[RoutineProposal, ...]:
        return tuple(
            item for item in self.proposals.values() if item.routine_id == routine_id
        )

    def list_proposals(self) -> tuple[RoutineProposal, ...]:
        return tuple(self.proposals.values())

    def save_run(self, run: RoutineRun) -> None:
        self.runs[run.id] = run

    def get_run(self, run_id: str) -> RoutineRun:
        try:
            return self.runs[run_id]
        except KeyError as error:
            raise KeyError(f"Unknown routine run: {run_id}") from error

    def find_run_by_key(self, idempotency_key: str) -> RoutineRun | None:
        return next(
            (item for item in self.runs.values() if item.idempotency_key == idempotency_key),
            None,
        )

    def save_receipt(self, receipt: RoutineReceipt) -> None:
        self.receipts[receipt.run_id] = receipt

    def get_receipt(self, run_id: str) -> RoutineReceipt | None:
        return self.receipts.get(run_id)


class JsonFileAutomationRepository(InMemoryAutomationRepository):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        data = read_json(
            path,
            default={"schema_version": 1, "proposals": [], "runs": [], "receipts": []},
        )
        if data.get("schema_version") != 1:
            raise RuntimeError("Unsupported automation storage schema.")
        self.proposals = {
            str(item["id"]): _proposal_from_data(item) for item in data["proposals"]
        }
        self.runs = {str(item["id"]): _run_from_data(item) for item in data["runs"]}
        self.receipts = {
            str(item["run_id"]): _receipt_from_data(item)
            for item in data["receipts"]
        }

    def save_proposal(self, proposal: RoutineProposal) -> None:
        super().save_proposal(proposal)
        self._flush()

    def save_run(self, run: RoutineRun) -> None:
        super().save_run(run)
        self._flush()

    def save_receipt(self, receipt: RoutineReceipt) -> None:
        super().save_receipt(receipt)
        self._flush()

    def _flush(self) -> None:
        atomic_write_json(
            self._path,
            {
                "schema_version": 1,
                "proposals": [_proposal_to_data(item) for item in self.proposals.values()],
                "runs": [_run_to_data(item) for item in self.runs.values()],
                "receipts": [_receipt_to_data(item) for item in self.receipts.values()],
            },
        )


class RoutineService:
    def __init__(self, repository: AutomationRepository) -> None:
        self._repository = repository

    def compile_request(
        self,
        source_request: str,
        *,
        compiler: RoutineCompiler,
        proposal_id: str,
        routine_id: str,
        created_at: datetime,
    ) -> RoutineProposal:
        request = source_request.strip()
        if not request:
            raise ValueError("A natural-language recurring request is required.")
        draft = compiler.compile(request)
        return self.propose(
            proposal_id=proposal_id,
            routine_id=routine_id,
            source_request=request,
            created_at=created_at,
            trigger=draft.trigger,
            source_scope=draft.source_scope,
            context_scope=draft.context_scope,
            tools=draft.tools,
            permissions=draft.permissions,
            delivery=draft.delivery,
            model_policy=draft.model_policy,
            cost_ceiling=draft.cost_ceiling,
            retry_limit=draft.retry_limit,
            delegation_policy=draft.delegation_policy,
            failure_behavior=draft.failure_behavior,
        )

    def propose(
        self,
        *,
        proposal_id: str,
        routine_id: str,
        source_request: str,
        created_at: datetime,
        trigger: str,
        source_scope: tuple[str, ...],
        context_scope: tuple[str, ...],
        tools: tuple[str, ...],
        permissions: tuple[str, ...],
        delivery: str,
        model_policy: str,
        cost_ceiling: int,
        retry_limit: int,
        delegation_policy: str,
        failure_behavior: str,
    ) -> RoutineProposal:
        previous = self._repository.proposals_for(routine_id)
        proposal = RoutineProposal(
            id=proposal_id,
            routine_id=routine_id,
            version=max((item.version for item in previous), default=0) + 1,
            source_request=source_request.strip(),
            created_at=created_at,
            trigger=trigger.strip(),
            source_scope=source_scope,
            context_scope=context_scope,
            tools=tools,
            permissions=permissions,
            delivery=delivery.strip(),
            model_policy=model_policy.strip(),
            cost_ceiling=cost_ceiling,
            retry_limit=retry_limit,
            delegation_policy=delegation_policy.strip(),
            failure_behavior=failure_behavior.strip(),
        )
        self._validate_contract(proposal)
        self._repository.save_proposal(proposal)
        return proposal

    def confirm(self, proposal_id: str) -> RoutineProposal:
        proposal = self._repository.get_proposal(proposal_id)
        self._validate_contract(proposal)
        if proposal.status != "proposed":
            raise ValueError(f"Cannot confirm a {proposal.status} routine proposal.")
        if proposal.supersedes_proposal_id is not None:
            previous = self._repository.get_proposal(proposal.supersedes_proposal_id)
            if previous.status == "confirmed":
                self._repository.save_proposal(replace(previous, status="superseded"))
        confirmed = replace(proposal, status="confirmed")
        self._repository.save_proposal(confirmed)
        return confirmed

    def revise(
        self,
        confirmed_proposal_id: str,
        *,
        proposal_id: str,
        created_at: datetime,
        changes: dict[str, Any],
    ) -> RoutineProposal:
        current = self._repository.get_proposal(confirmed_proposal_id)
        if current.status != "confirmed":
            raise ValueError("Only a confirmed routine contract can be revised.")
        allowed_changes = {
            "source_request",
            "trigger",
            "source_scope",
            "context_scope",
            "tools",
            "permissions",
            "delivery",
            "model_policy",
            "cost_ceiling",
            "retry_limit",
            "delegation_policy",
            "failure_behavior",
        }
        if unknown := set(changes) - allowed_changes:
            raise ValueError(
                "Routine revision contains unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        candidate = replace(
            current,
            **changes,
            id=proposal_id,
            version=current.version + 1,
            created_at=created_at,
            status="proposed",
            supersedes_proposal_id=current.id,
        )
        self._validate_contract(candidate)
        self._repository.save_proposal(candidate)
        return candidate

    @staticmethod
    def _validate_contract(proposal: RoutineProposal) -> None:
        required = {
            "routine id": proposal.routine_id,
            "source request": proposal.source_request,
            "trigger": proposal.trigger,
            "delivery": proposal.delivery,
            "model policy": proposal.model_policy,
            "delegation policy": proposal.delegation_policy,
            "failure behavior": proposal.failure_behavior,
        }
        for label, value in required.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Routine {label} cannot be empty.")
        for label, values in (
            ("source scope", proposal.source_scope),
            ("context scope", proposal.context_scope),
            ("tools", proposal.tools),
            ("permissions", proposal.permissions),
        ):
            if not isinstance(values, tuple) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise ValueError(f"Routine {label} must contain named values.")
        if not isinstance(proposal.cost_ceiling, int) or not isinstance(
            proposal.retry_limit, int
        ):
            raise ValueError("Routine cost and retry limits must be integers.")
        if proposal.cost_ceiling < 0 or proposal.retry_limit < 0:
            raise ValueError("Routine cost and retry limits cannot be negative.")

    def inspect_proposal(self, proposal_id: str) -> RoutineProposal:
        return self._repository.get_proposal(proposal_id)

    def pending_confirmation_ids(self) -> tuple[str, ...]:
        """Return proposed routine contracts that still need user confirmation."""
        return tuple(
            proposal.id
            for proposal in self._repository.list_proposals()
            if proposal.status == "proposed"
        )

    def start_run(
        self,
        *,
        run_id: str,
        proposal_id: str,
        scheduled_for: datetime,
        idempotency_key: str,
        steps: tuple[RoutineStep, ...],
        triggered_by: str,
    ) -> RoutineRun:
        proposal = self._repository.get_proposal(proposal_id)
        if proposal.status != "confirmed":
            raise PermissionError("A routine must be confirmed before it can run.")
        if triggered_by != proposal.trigger:
            raise PermissionError("The routine run does not match its confirmed trigger.")
        existing = self._repository.find_run_by_key(idempotency_key)
        if existing is not None:
            return existing
        if not steps:
            raise ValueError("A routine run requires at least one step.")
        unauthorized_tools = tuple(
            step.action for step in steps if step.action not in proposal.tools
        )
        if unauthorized_tools:
            raise PermissionError(
                "Routine steps exceed the confirmed tool scope: "
                + ", ".join(unauthorized_tools)
            )
        if any(step.kind == "model" for step in steps) and "model" not in (
            proposal.model_policy.casefold()
        ):
            raise PermissionError("The confirmed routine policy does not allow a model step.")
        run = RoutineRun(run_id, proposal_id, scheduled_for, idempotency_key, steps)
        self._repository.save_run(run)
        return run

    def resume_run(
        self,
        run_id: str,
        *,
        deterministic_executor: StepExecutor,
        model_executor: StepExecutor | None,
        completed_at: datetime,
    ) -> RoutineReceipt:
        existing_receipt = self._repository.get_receipt(run_id)
        if existing_receipt is not None:
            return existing_receipt
        run = self._repository.get_run(run_id)
        proposal = self._repository.get_proposal(run.proposal_id)
        results = list(run.results)
        total_cost = sum(item.cost_units for item in results)

        for index in range(run.current_step, len(run.steps)):
            step = run.steps[index]
            executor = deterministic_executor if step.kind == "deterministic" else model_executor
            max_attempts = proposal.retry_limit + 1
            step_result: StepResult | None = None
            while run.current_step_attempts < max_attempts:
                if executor is None:
                    step_result = StepResult("failed", "No model executor is configured.")
                    break
                quoted_cost = _quote_step_cost(executor, step)
                if total_cost + quoted_cost > proposal.cost_ceiling:
                    step_result = StepResult(
                        "blocked", "Routine cost ceiling would be exceeded."
                    )
                    break
                run = replace(
                    run,
                    attempts=run.attempts + 1,
                    current_step_attempts=run.current_step_attempts + 1,
                )
                self._repository.save_run(run)
                try:
                    step_result = executor.execute(
                        step, f"{run.idempotency_key}:{step.id}"
                    )
                    if (
                        step_result.cost_units > quoted_cost
                        or total_cost + step_result.cost_units
                        > proposal.cost_ceiling
                    ):
                        step_result = StepResult(
                            "blocked",
                            "Routine executor exceeded its preflight cost quote.",
                            cost_units=step_result.cost_units,
                            model_calls=step_result.model_calls,
                        )
                    break
                except InterruptedError:
                    raise
                except Exception as error:  # adapter failures become bounded evidence
                    step_result = StepResult("failed", str(error))
            if step_result is None:
                step_result = StepResult("failed", "Retry limit exhausted.")
            total_cost += step_result.cost_units
            results.append(step_result)
            run = replace(
                run,
                current_step=index + 1,
                current_step_attempts=0,
                results=tuple(results),
            )
            self._repository.save_run(run)
            if step_result.status in ("blocked", "failed"):
                break

        status = _aggregate_run_status(tuple(results), len(run.steps))
        terminal = replace(run, status=status, results=tuple(results))
        self._repository.save_run(terminal)
        receipt = RoutineReceipt(
            run.id,
            run.proposal_id,
            status,
            completed_at,
            run.attempts,
            tuple(results),
        )
        self._repository.save_receipt(receipt)
        return receipt

    def inspect_run(self, run_id: str) -> RoutineRun:
        return self._repository.get_run(run_id)

    def record_no_run(
        self, *, proposal_id: str, evaluated_at: datetime, reason: str
    ) -> NoRunReceipt:
        self._repository.get_proposal(proposal_id)
        if not reason.strip():
            raise ValueError("A no-run result requires a truthful reason.")
        return NoRunReceipt(proposal_id, evaluated_at, reason.strip())


@dataclass(frozen=True)
class WatchProposal:
    id: str
    source_request_id: str
    trigger: str
    source_scope: tuple[str, ...]
    notification_threshold: float
    budget: int
    delivery_policy: str
    open_ended: bool
    confirmed: bool = False


@dataclass(frozen=True)
class WatchFinding:
    id: str
    source: str
    goal_or_decision_id: str
    summary: str
    credibility: float
    relevance: float
    cost_units: int
    evidence: str


@dataclass(frozen=True)
class WatchReceipt:
    id: str
    watch_id: str
    checked_at: datetime
    notified: tuple[WatchFinding, ...]
    suppressed: tuple[WatchFinding, ...]
    total_cost_units: int
    check_in_proposed: bool
    status: Literal["success", "blocked"]
    no_material_change: bool


class WatchService:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._watches: dict[str, WatchProposal] = {}
        self._receipts: dict[str, WatchReceipt] = {}
        if path is not None:
            data = read_json(
                path,
                default={"schema_version": 1, "watches": [], "receipts": []},
            )
            if data.get("schema_version") != 1:
                raise RuntimeError("Unsupported watch storage schema.")
            self._watches = {
                str(item["id"]): _watch_proposal_from_data(item)
                for item in data["watches"]
            }
            self._receipts = {
                str(item["id"]): _watch_receipt_from_data(item)
                for item in data["receipts"]
            }

    def propose(
        self,
        *,
        watch_id: str,
        source_request_id: str,
        trigger: str,
        source_scope: tuple[str, ...],
        notification_threshold: float,
        budget: int,
        delivery_policy: str,
        open_ended: bool,
    ) -> WatchProposal:
        if not 0 <= notification_threshold <= 1:
            raise ValueError("The notification threshold must be between 0 and 1.")
        if budget < 0:
            raise ValueError("A watch budget cannot be negative.")
        proposal = WatchProposal(
            watch_id,
            source_request_id,
            trigger,
            source_scope,
            notification_threshold,
            budget,
            delivery_policy,
            open_ended,
        )
        self._watches[proposal.id] = proposal
        self._flush()
        return proposal

    def confirm(self, watch_id: str) -> WatchProposal:
        proposal = self._watches[watch_id]
        confirmed = replace(proposal, confirmed=True)
        self._watches[watch_id] = confirmed
        self._flush()
        return confirmed

    def run(
        self,
        watch_id: str,
        *,
        run_id: str,
        triggered_by: str,
        quoted_cost_units: int,
        findings: tuple[WatchFinding, ...],
        checked_at: datetime,
    ) -> WatchReceipt:
        watch = self._watches[watch_id]
        if not watch.confirmed:
            raise PermissionError("A watch must be confirmed before it can run.")
        if triggered_by != watch.trigger:
            raise PermissionError("The watch run does not match its confirmed trigger.")
        if quoted_cost_units < 0:
            raise ValueError("A watch cost quote cannot be negative.")
        if quoted_cost_units > watch.budget:
            receipt = WatchReceipt(
                run_id,
                watch_id,
                checked_at,
                (),
                findings,
                0,
                False,
                "blocked",
                True,
            )
            self._receipts[run_id] = receipt
            self._flush()
            return receipt
        if any(item.source not in watch.source_scope for item in findings):
            raise PermissionError("A finding falls outside the watch source scope.")
        total_cost = sum(item.cost_units for item in findings)
        if total_cost > quoted_cost_units:
            raise RuntimeError("Watch source exceeded its preflight cost quote.")
        if total_cost > watch.budget:
            receipt = WatchReceipt(
                run_id,
                watch_id,
                checked_at,
                (),
                findings,
                total_cost,
                False,
                "blocked",
                True,
            )
        else:
            notified = tuple(
                item
                for item in findings
                if item.credibility >= watch.notification_threshold
                and item.relevance >= watch.notification_threshold
                and item.goal_or_decision_id.strip()
            )
            suppressed = tuple(item for item in findings if item not in notified)
            receipt = WatchReceipt(
                run_id,
                watch_id,
                checked_at,
                notified,
                suppressed,
                total_cost,
                bool(notified and watch.open_ended),
                "success",
                not notified,
            )
        self._receipts[run_id] = receipt
        self._flush()
        return receipt

    def inspect(self, watch_id: str) -> WatchProposal:
        return self._watches[watch_id]

    def receipt(self, run_id: str) -> WatchReceipt:
        return self._receipts[run_id]

    def _flush(self) -> None:
        if self._path is None:
            return
        atomic_write_json(
            self._path,
            {
                "schema_version": 1,
                "watches": [asdict(item) for item in self._watches.values()],
                "receipts": [
                    _watch_receipt_to_data(item) for item in self._receipts.values()
                ],
            },
        )


@dataclass(frozen=True)
class BriefingPolicy:
    max_items: int
    max_cost_units: int
    delivery_preference: str


@dataclass(frozen=True)
class BriefingFinding:
    id: str
    summary: str
    goal_relevance: float
    evidence_quality: float
    novelty: float
    urgency: float
    cost_units: int
    attention_burden: float
    delivery: str


@dataclass(frozen=True)
class BriefingItem:
    finding: BriefingFinding
    explanation: str


@dataclass(frozen=True)
class BriefingReceipt:
    run_id: str
    delivered_at: datetime
    status: Literal["success"]
    items: tuple[BriefingItem, ...]
    suppressed_ids: tuple[str, ...]
    total_cost_units: int
    no_material_change: bool
    executed: bool = True


class BriefingService:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._receipts: dict[str, BriefingReceipt] = {}
        if path is not None:
            data = read_json(
                path,
                default={"schema_version": 1, "receipts": []},
            )
            if data.get("schema_version") != 1:
                raise RuntimeError("Unsupported briefing storage schema.")
            self._receipts = {
                str(item["run_id"]): _briefing_receipt_from_data(item)
                for item in data["receipts"]
            }

    def deliver(
        self,
        *,
        run_id: str,
        policy: BriefingPolicy,
        findings: tuple[BriefingFinding, ...],
        delivered_at: datetime,
    ) -> BriefingReceipt:
        if policy.max_items < 0 or policy.max_cost_units < 0:
            raise ValueError("Briefing resource caps cannot be negative.")
        ranked = sorted(
            findings,
            key=lambda item: (
                item.delivery == policy.delivery_preference,
                item.goal_relevance,
                item.evidence_quality,
                item.novelty,
                item.urgency,
                -item.attention_burden,
                -item.cost_units,
            ),
            reverse=True,
        )
        selected: list[BriefingItem] = []
        total_cost = 0
        for finding in ranked:
            if len(selected) >= policy.max_items:
                continue
            if total_cost + finding.cost_units > policy.max_cost_units:
                continue
            total_cost += finding.cost_units
            selected.append(
                BriefingItem(
                    finding,
                    "Included for goal relevance, evidence, novelty, urgency, "
                    "resource cost, attention burden, and delivery fit.",
                )
            )
        selected_ids = {item.finding.id for item in selected}
        receipt = BriefingReceipt(
            run_id,
            delivered_at,
            "success",
            tuple(selected),
            tuple(item.id for item in findings if item.id not in selected_ids),
            total_cost,
            not selected,
        )
        self._receipts[run_id] = receipt
        self._flush()
        return receipt

    def receipt(self, run_id: str) -> BriefingReceipt:
        return self._receipts[run_id]

    def _flush(self) -> None:
        if self._path is None:
            return
        atomic_write_json(
            self._path,
            {
                "schema_version": 1,
                "receipts": [
                    _briefing_receipt_to_data(item)
                    for item in self._receipts.values()
                ],
            },
        )


def _aggregate_run_status(
    results: tuple[StepResult, ...], total_steps: int
) -> Literal["success", "partial", "blocked", "failed"]:
    if any(item.status == "blocked" for item in results):
        return "blocked"
    if any(item.status == "failed" for item in results):
        return "partial" if any(item.status == "success" for item in results) else "failed"
    if len(results) < total_steps or any(item.status == "partial" for item in results):
        return "partial"
    return "success"


def _quote_step_cost(executor: StepExecutor, step: RoutineStep) -> int:
    quote_method = getattr(executor, "quote_cost", None)
    if quote_method is None:
        if step.kind == "model":
            raise RuntimeError("A model routine step requires a preflight cost quote.")
        return 0
    quoted_cost = quote_method(step)
    if not isinstance(quoted_cost, int) or quoted_cost < 0:
        raise RuntimeError("A routine cost quote must be a non-negative integer.")
    return quoted_cost


def _proposal_to_data(value: RoutineProposal) -> dict[str, object]:
    data = asdict(value)
    data["created_at"] = value.created_at.isoformat()
    return data


def _proposal_from_data(data: dict[str, Any]) -> RoutineProposal:
    return RoutineProposal(
        id=str(data["id"]),
        routine_id=str(data["routine_id"]),
        version=int(data["version"]),
        source_request=str(data["source_request"]),
        created_at=datetime.fromisoformat(str(data["created_at"])),
        trigger=str(data["trigger"]),
        source_scope=tuple(str(item) for item in data["source_scope"]),
        context_scope=tuple(str(item) for item in data["context_scope"]),
        tools=tuple(str(item) for item in data["tools"]),
        permissions=tuple(str(item) for item in data["permissions"]),
        delivery=str(data["delivery"]),
        model_policy=str(data["model_policy"]),
        cost_ceiling=int(data["cost_ceiling"]),
        retry_limit=int(data["retry_limit"]),
        delegation_policy=str(data["delegation_policy"]),
        failure_behavior=str(data["failure_behavior"]),
        status=data["status"],
        supersedes_proposal_id=(
            str(data["supersedes_proposal_id"])
            if data.get("supersedes_proposal_id") is not None
            else None
        ),
    )


def _run_to_data(value: RoutineRun) -> dict[str, object]:
    data = asdict(value)
    data["scheduled_for"] = value.scheduled_for.isoformat()
    return data


def _run_from_data(data: dict[str, Any]) -> RoutineRun:
    return RoutineRun(
        id=str(data["id"]),
        proposal_id=str(data["proposal_id"]),
        scheduled_for=datetime.fromisoformat(str(data["scheduled_for"])),
        idempotency_key=str(data["idempotency_key"]),
        steps=tuple(
            RoutineStep(str(item["id"]), item["kind"], str(item["action"]))
            for item in data["steps"]
        ),
        status=data["status"],
        current_step=int(data["current_step"]),
        attempts=int(data["attempts"]),
        current_step_attempts=int(data.get("current_step_attempts", 0)),
        results=tuple(
            StepResult(
                item["status"],
                str(item["detail"]),
                int(item.get("cost_units", 0)),
                int(item.get("model_calls", 0)),
            )
            for item in data["results"]
        ),
    )


def _receipt_to_data(value: RoutineReceipt) -> dict[str, object]:
    data = asdict(value)
    data["completed_at"] = value.completed_at.isoformat()
    return data


def _receipt_from_data(data: dict[str, Any]) -> RoutineReceipt:
    return RoutineReceipt(
        run_id=str(data["run_id"]),
        proposal_id=str(data["proposal_id"]),
        status=data["status"],
        completed_at=datetime.fromisoformat(str(data["completed_at"])),
        attempts=int(data["attempts"]),
        results=tuple(
            StepResult(
                item["status"],
                str(item["detail"]),
                int(item.get("cost_units", 0)),
                int(item.get("model_calls", 0)),
            )
            for item in data["results"]
        ),
        executed=bool(data.get("executed", True)),
    )


def _watch_proposal_from_data(data: dict[str, Any]) -> WatchProposal:
    return WatchProposal(
        id=str(data["id"]),
        source_request_id=str(data["source_request_id"]),
        trigger=str(data["trigger"]),
        source_scope=tuple(str(item) for item in data["source_scope"]),
        notification_threshold=float(data["notification_threshold"]),
        budget=int(data["budget"]),
        delivery_policy=str(data["delivery_policy"]),
        open_ended=bool(data["open_ended"]),
        confirmed=bool(data.get("confirmed", False)),
    )


def _watch_finding_from_data(data: dict[str, Any]) -> WatchFinding:
    return WatchFinding(
        id=str(data["id"]),
        source=str(data["source"]),
        goal_or_decision_id=str(data["goal_or_decision_id"]),
        summary=str(data["summary"]),
        credibility=float(data["credibility"]),
        relevance=float(data["relevance"]),
        cost_units=int(data["cost_units"]),
        evidence=str(data["evidence"]),
    )


def _watch_receipt_to_data(value: WatchReceipt) -> dict[str, object]:
    data = asdict(value)
    data["checked_at"] = value.checked_at.isoformat()
    return data


def _watch_receipt_from_data(data: dict[str, Any]) -> WatchReceipt:
    return WatchReceipt(
        id=str(data["id"]),
        watch_id=str(data["watch_id"]),
        checked_at=datetime.fromisoformat(str(data["checked_at"])),
        notified=tuple(_watch_finding_from_data(item) for item in data["notified"]),
        suppressed=tuple(
            _watch_finding_from_data(item) for item in data["suppressed"]
        ),
        total_cost_units=int(data["total_cost_units"]),
        check_in_proposed=bool(data["check_in_proposed"]),
        status=data["status"],
        no_material_change=bool(data["no_material_change"]),
    )


def _briefing_finding_from_data(data: dict[str, Any]) -> BriefingFinding:
    return BriefingFinding(
        id=str(data["id"]),
        summary=str(data["summary"]),
        goal_relevance=float(data["goal_relevance"]),
        evidence_quality=float(data["evidence_quality"]),
        novelty=float(data["novelty"]),
        urgency=float(data["urgency"]),
        cost_units=int(data["cost_units"]),
        attention_burden=float(data["attention_burden"]),
        delivery=str(data["delivery"]),
    )


def _briefing_receipt_to_data(value: BriefingReceipt) -> dict[str, object]:
    data = asdict(value)
    data["delivered_at"] = value.delivered_at.isoformat()
    return data


def _briefing_receipt_from_data(data: dict[str, Any]) -> BriefingReceipt:
    return BriefingReceipt(
        run_id=str(data["run_id"]),
        delivered_at=datetime.fromisoformat(str(data["delivered_at"])),
        status=data["status"],
        items=tuple(
            BriefingItem(
                _briefing_finding_from_data(item["finding"]),
                str(item["explanation"]),
            )
            for item in data["items"]
        ),
        suppressed_ids=tuple(str(item) for item in data["suppressed_ids"]),
        total_cost_units=int(data["total_cost_units"]),
        no_material_change=bool(data["no_material_change"]),
        executed=bool(data.get("executed", True)),
    )
