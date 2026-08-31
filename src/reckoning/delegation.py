from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from reckoning.json_store import atomic_write_json, read_json

ExecutionMode = Literal[
    "direct", "budget_bounded_delegation", "approval_before_delegation"
]
ReviewStatus = Literal["completed", "blocked", "limited"]
DispositionStatus = Literal["accepted", "rejected", "blocked", "limited"]


@dataclass(frozen=True)
class DelegationEvidence:
    id: str
    claim: str
    source: str

    def __post_init__(self) -> None:
        _required(self.id, "Evidence id")
        _required(self.claim, "Evidence claim")
        _required(self.source, "Evidence source")


@dataclass(frozen=True)
class DelegatedWorkerResult:
    """A worker finding for Simon to review, never a user-facing answer."""

    finding: str
    evidence: tuple[DelegationEvidence, ...]
    provider: str
    model_calls: int
    cost: Decimal
    retries: int
    tools_used: tuple[str, ...]

    def __post_init__(self) -> None:
        _required(self.finding, "Worker finding")
        _required(self.provider, "Worker provider")
        if self.model_calls < 0:
            raise ValueError("Worker model calls cannot be negative.")
        if self.cost < 0:
            raise ValueError("Worker cost cannot be negative.")
        if self.retries < 0:
            raise ValueError("Worker retries cannot be negative.")
        _unique(self.tools_used, "Worker tools")
        evidence_ids = tuple(item.id for item in self.evidence)
        _unique(evidence_ids, "Worker evidence ids")


@dataclass(frozen=True)
class DelegationPolicy:
    mode: ExecutionMode
    allowed_context_categories: frozenset[str]
    allowed_tools: frozenset[str]
    allowed_providers: frozenset[str]
    max_model_calls: int
    max_cost: Decimal
    max_retries: int
    max_tool_calls: int = 1

    def __post_init__(self) -> None:
        if self.max_model_calls < 0:
            raise ValueError("The policy model-call limit cannot be negative.")
        if self.max_cost < 0:
            raise ValueError("The policy cost limit cannot be negative.")
        if self.max_retries < 0:
            raise ValueError("The policy retry limit cannot be negative.")
        if self.max_tool_calls < 0:
            raise ValueError("The policy tool-call limit cannot be negative.")
        reserved = sorted(tool for tool in self.allowed_tools if _is_owner_tool(tool))
        if reserved:
            raise ValueError(
                "These tools are reserved for Simon: " + ", ".join(reserved)
            )


@dataclass(frozen=True)
class DelegationRequest:
    purpose: str
    instructions: str
    context_categories: tuple[str, ...]
    tools: tuple[str, ...]
    provider: str
    model_call_limit: int
    cost_limit: Decimal
    retry_limit: int
    approval_id: str | None = None
    context: tuple[DelegatedContextRequest, ...] = ()
    tool_call_limit: int = 1

    def __post_init__(self) -> None:
        _required(self.purpose, "Delegation purpose")
        _required(self.instructions, "Delegation instructions")
        _required(self.provider, "Delegation provider")
        _unique(self.context_categories, "Context categories")
        _unique(self.tools, "Delegation tools")
        if self.model_call_limit < 0:
            raise ValueError("The request model-call limit cannot be negative.")
        if self.cost_limit < 0:
            raise ValueError("The request cost limit cannot be negative.")
        if self.retry_limit < 0:
            raise ValueError("The request retry limit cannot be negative.")
        if self.tool_call_limit < 0:
            raise ValueError("The request tool-call limit cannot be negative.")
        if (
            self.context
            and tuple(item.category for item in self.context)
            != self.context_categories
        ):
            raise ValueError("Exact context selections must match context categories.")
        if self.context_categories and not self.context:
            raise ValueError("Delegation requires exact context entry selections.")


@dataclass(frozen=True)
class DelegatedContextRequest:
    category: str
    entry_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _required(self.category, "Context category")
        if not self.entry_ids:
            raise ValueError("A delegated context selection needs an entry id.")
        _unique(self.entry_ids, "Context entry ids")


@dataclass(frozen=True)
class DelegatedContextEntry:
    id: str
    content: str

    def __post_init__(self) -> None:
        _required(self.id, "Context entry id")
        _required(self.content, "Context entry content")


@dataclass(frozen=True)
class DelegatedContext:
    category: str
    entries: tuple[DelegatedContextEntry | str, ...]


@dataclass(frozen=True)
class DelegatedTask:
    """Minimum task context and ceilings, with no identity or authority handles."""

    purpose: str
    instructions: str
    context: tuple[DelegatedContext, ...]
    tools: tuple[str, ...]
    provider: str
    model_call_limit: int
    cost_limit: Decimal
    retry_limit: int
    tool_call_limit: int = 1


@dataclass(frozen=True)
class DelegatedWorkerFinding:
    finding: str
    evidence: tuple[DelegationEvidence, ...]

    def __post_init__(self) -> None:
        _required(self.finding, "Worker finding")
        _unique(tuple(item.id for item in self.evidence), "Worker evidence ids")


@dataclass(frozen=True)
class DelegatedModelResponse:
    content: str
    cost: Decimal

    def __post_init__(self) -> None:
        _required(self.content, "Delegated model response")
        if self.cost < 0:
            raise ValueError("Delegated model cost cannot be negative.")


@dataclass(frozen=True)
class DelegationReview:
    purpose: str
    status: ReviewStatus
    worker_result: DelegatedWorkerResult | None
    policy_violations: tuple[str, ...]
    verified_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentDisposition:
    status: DispositionStatus
    final_response: str
    rationale: str
    verified_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required(self.final_response, "The Reckoning agent final response")
        _required(self.rationale, "The Reckoning agent rationale")
        _unique(self.verified_evidence_ids, "Verified evidence ids")


@dataclass(frozen=True)
class DelegationReceipt:
    id: str
    started_at: datetime
    purpose: str
    context_categories: tuple[str, ...]
    provider: str
    tools_allowed: tuple[str, ...]
    tools_used: tuple[str, ...]
    model_calls: int
    cost: Decimal
    retries: int
    result: DispositionStatus
    rationale: str
    verified_evidence_ids: tuple[str, ...]
    policy_violations: tuple[str, ...]
    tool_calls: int = 0


@dataclass(frozen=True)
class DelegationOutcome:
    """The only user-facing delegation result: Simon's response and the receipt."""

    response: str
    receipt: DelegationReceipt


class DelegationContextSource(Protocol):
    def read_category(self, category: str) -> tuple[str, ...]: ...

    def read_entries(
        self, category: str, entry_ids: tuple[str, ...]
    ) -> tuple[DelegatedContextEntry, ...]: ...


class DelegatedWorker(Protocol):
    def execute(
        self, task: DelegatedTask, runtime: BoundedWorkerRuntime
    ) -> DelegatedWorkerFinding: ...


class ReckoningAgentReviewer(Protocol):
    def verify_evidence(self, evidence: DelegationEvidence) -> bool: ...

    def dispose(self, review: DelegationReview) -> AgentDisposition: ...


class DelegatedModelGateway(Protocol):
    def quote_max_cost(self, provider: str, prompt: str) -> Decimal: ...

    def execute(self, provider: str, prompt: str) -> DelegatedModelResponse: ...


class DelegatedToolGateway(Protocol):
    def execute(self, tool: str, payload: str) -> str: ...


class DelegationLimitExceeded(RuntimeError):
    pass


class BoundedWorkerRuntime:
    """Host-owned capabilities; every external operation is gated before use."""

    def __init__(
        self,
        task: DelegatedTask,
        model_gateway: DelegatedModelGateway,
        tool_gateway: DelegatedToolGateway,
    ) -> None:
        self._task = task
        self._model_gateway = model_gateway
        self._tool_gateway = tool_gateway
        self.model_calls = 0
        self.cost = Decimal(0)
        self.retries = 0
        self.tools_used: list[str] = []
        self.tool_calls = 0

    def call_model(self, prompt: str) -> str:
        _required(prompt, "Delegated model prompt")
        if self.model_calls >= self._task.model_call_limit:
            raise DelegationLimitExceeded("worker exceeded the model-call limit")
        quote = self._model_gateway.quote_max_cost(self._task.provider, prompt)
        if quote < 0:
            raise RuntimeError("The delegated model gateway returned a negative quote.")
        if self.cost + quote > self._task.cost_limit:
            raise DelegationLimitExceeded("worker exceeded the cost limit")
        self.model_calls += 1
        response = self._model_gateway.execute(self._task.provider, prompt)
        if response.cost > quote:
            raise RuntimeError("The delegated model gateway exceeded its cost quote.")
        self.cost += response.cost
        return response.content

    def use_tool(self, tool: str, payload: str) -> str:
        if tool not in self._task.tools:
            raise DelegationLimitExceeded(
                "worker used a tool outside the delegated tool scope: " + tool
            )
        if self.tool_calls >= self._task.tool_call_limit:
            raise DelegationLimitExceeded("worker exceeded the tool-call limit")
        self.tool_calls += 1
        if tool not in self.tools_used:
            self.tools_used.append(tool)
        return self._tool_gateway.execute(tool, payload)

    def record_retry(self) -> None:
        if self.retries >= self._task.retry_limit:
            raise DelegationLimitExceeded("worker exceeded the retry limit")
        self.retries += 1


class DelegationReceiptRepository(Protocol):
    def save(self, receipt: DelegationReceipt) -> None: ...

    def get(self, receipt_id: str) -> DelegationReceipt: ...


class InMemoryDelegationReceiptRepository:
    def __init__(self) -> None:
        self._receipts: dict[str, DelegationReceipt] = {}

    def save(self, receipt: DelegationReceipt) -> None:
        self._receipts[receipt.id] = receipt

    def get(self, receipt_id: str) -> DelegationReceipt:
        try:
            return self._receipts[receipt_id]
        except KeyError as error:
            raise KeyError(f"Unknown delegation receipt: {receipt_id}") from error


class JsonFileDelegationReceiptRepository(InMemoryDelegationReceiptRepository):
    """Durable receipts without delegated context contents or worker findings."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        data = read_json(path, default={"schema_version": 1, "receipts": []})
        if data.get("schema_version") != 1:
            raise RuntimeError("Unsupported delegation receipt storage schema.")
        stored_receipts = data.get("receipts")
        if not isinstance(stored_receipts, list):
            raise RuntimeError("Stored delegation receipts are invalid.")
        try:
            self._receipts = {
                str(item["id"]): _receipt_from_data(item)
                for item in stored_receipts
                if isinstance(item, dict)
            }
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("Stored delegation receipts are invalid.") from error
        if len(self._receipts) != len(stored_receipts):
            raise RuntimeError("Stored delegation receipts are invalid.")

    def save(self, receipt: DelegationReceipt) -> None:
        super().save(receipt)
        atomic_write_json(
            self._path,
            {
                "schema_version": 1,
                "receipts": [
                    _receipt_to_data(item) for item in self._receipts.values()
                ],
            },
        )


class DelegationCoordinator:
    """Runs one temporary worker under policy and returns Simon's disposition."""

    def __init__(
        self,
        context_source: DelegationContextSource,
        worker: DelegatedWorker,
        reviewer: ReckoningAgentReviewer,
        receipts: DelegationReceiptRepository,
        *,
        model_gateway: DelegatedModelGateway | None = None,
        tool_gateway: DelegatedToolGateway | None = None,
    ) -> None:
        self._context_source = context_source
        self._worker = worker
        self._reviewer = reviewer
        self._receipts = receipts
        self._model_gateway = model_gateway
        self._tool_gateway = tool_gateway

    def delegate(
        self,
        request: DelegationRequest,
        policy: DelegationPolicy,
        *,
        receipt_id: str,
        started_at: datetime,
    ) -> DelegationOutcome:
        _required(receipt_id, "Delegation receipt id")
        violations = self._request_violations(request, policy)
        result: DelegatedWorkerResult | None = None
        model_calls = 0
        cost = Decimal(0)
        retries = 0
        tools_used: tuple[str, ...] = ()
        tool_calls = 0
        verified_evidence_ids: tuple[str, ...] = ()

        if violations:
            review_status: ReviewStatus = "blocked"
        else:
            if self._model_gateway is None or self._tool_gateway is None:
                violations = ("bounded delegation gateways are unavailable",)
                review_status = "blocked"
            delegated_context: list[DelegatedContext] = []
            if not violations:
                try:
                    for selection in request.context:
                        entries = self._context_source.read_entries(
                            selection.category, selection.entry_ids
                        )
                        returned_ids = tuple(entry.id for entry in entries)
                        if returned_ids != selection.entry_ids:
                            violations = (
                                "context source returned entries outside the exact selection",
                            )
                            break
                        delegated_context.append(
                            DelegatedContext(selection.category, entries)
                        )
                except Exception:
                    violations = ("delegated context selection failed safely",)
            if violations:
                review_status = "blocked"
            if not violations:
                assert self._model_gateway is not None
                assert self._tool_gateway is not None
                task = DelegatedTask(
                    purpose=request.purpose,
                    instructions=request.instructions,
                    context=tuple(delegated_context),
                    tools=request.tools,
                    provider=request.provider,
                    model_call_limit=request.model_call_limit,
                    cost_limit=request.cost_limit,
                    retry_limit=request.retry_limit,
                    tool_call_limit=request.tool_call_limit,
                )
                runtime = BoundedWorkerRuntime(
                    task, self._model_gateway, self._tool_gateway
                )
                try:
                    finding = self._worker.execute(task, runtime)
                    result = DelegatedWorkerResult(
                        finding=finding.finding,
                        evidence=finding.evidence,
                        provider=request.provider,
                        model_calls=runtime.model_calls,
                        cost=runtime.cost,
                        retries=runtime.retries,
                        tools_used=tuple(runtime.tools_used),
                    )
                except DelegationLimitExceeded as error:
                    violations = (str(error),)
                    review_status = "limited"
                except Exception:
                    violations = ("delegated execution failed safely",)
                    review_status = "limited"
                else:
                    unverified: list[str] = []
                    verified: list[str] = []
                    for evidence in result.evidence:
                        try:
                            evidence_is_verified = (
                                self._reviewer.verify_evidence(evidence) is True
                            )
                        except Exception:
                            evidence_is_verified = False
                            violations += (
                                "Simon evidence verification failed safely: "
                                + evidence.id,
                            )
                        if evidence_is_verified:
                            verified.append(evidence.id)
                        else:
                            unverified.append(evidence.id)
                    verified_evidence_ids = tuple(verified)
                    if unverified:
                        violations += (
                            "Simon could not verify worker evidence: "
                            + ", ".join(unverified),
                        )
                    review_status = "limited" if violations else "completed"
                model_calls = runtime.model_calls
                cost = runtime.cost
                retries = runtime.retries
                tools_used = tuple(runtime.tools_used)
                tool_calls = runtime.tool_calls

        review = DelegationReview(
            purpose=request.purpose,
            status=review_status,
            worker_result=result,
            policy_violations=violations,
            verified_evidence_ids=verified_evidence_ids,
        )
        try:
            disposition = self._reviewer.dispose(review)
            self._validate_disposition(review, disposition)
        except Exception:
            violations += ("Simon disposition review failed safely",)
            fallback_status: DispositionStatus = (
                "blocked" if review.status == "blocked" else "limited"
            )
            disposition = AgentDisposition(
                status=fallback_status,
                final_response=(
                    "Simon could not complete the accountable review. "
                    "No delegated result was accepted."
                ),
                rationale=(
                    "The disposition step failed, so the delegation failed closed."
                ),
            )

        receipt = DelegationReceipt(
            id=receipt_id,
            started_at=started_at,
            purpose=request.purpose,
            context_categories=request.context_categories,
            provider=request.provider,
            tools_allowed=request.tools,
            tools_used=tools_used,
            model_calls=model_calls,
            cost=cost,
            retries=retries,
            result=disposition.status,
            rationale=disposition.rationale,
            verified_evidence_ids=verified_evidence_ids,
            policy_violations=violations,
            tool_calls=tool_calls,
        )
        self._receipts.save(receipt)
        return DelegationOutcome(disposition.final_response, receipt)

    @staticmethod
    def _request_violations(
        request: DelegationRequest, policy: DelegationPolicy
    ) -> tuple[str, ...]:
        violations: list[str] = []
        if policy.mode == "direct":
            violations.append("the execution policy requires direct execution")
        if (
            policy.mode == "approval_before_delegation"
            and not (request.approval_id or "").strip()
        ):
            violations.append("delegation requires an explicit approval reference")
        denied_categories = sorted(
            set(request.context_categories) - policy.allowed_context_categories
        )
        if denied_categories:
            violations.append(
                "context falls outside the delegated scope: "
                + ", ".join(denied_categories)
            )
        denied_tools = sorted(set(request.tools) - policy.allowed_tools)
        if denied_tools:
            violations.append(
                "tool scope exceeds the execution policy: "
                + ", ".join(denied_tools)
            )
        if request.provider not in policy.allowed_providers:
            violations.append("provider falls outside the execution policy")
        if request.model_call_limit > policy.max_model_calls:
            violations.append("model-call limit exceeds the execution policy")
        if request.cost_limit > policy.max_cost:
            violations.append("cost limit exceeds the execution policy")
        if request.retry_limit > policy.max_retries:
            violations.append("retry limit exceeds the execution policy")
        if request.tool_call_limit > policy.max_tool_calls:
            violations.append("tool-call limit exceeds the execution policy")
        return tuple(violations)

    @staticmethod
    def _validate_disposition(
        review: DelegationReview, disposition: AgentDisposition
    ) -> None:
        allowed_statuses: dict[ReviewStatus, frozenset[DispositionStatus]] = {
            "completed": frozenset({"accepted", "rejected"}),
            "blocked": frozenset({"blocked", "rejected"}),
            "limited": frozenset({"limited", "rejected"}),
        }
        if disposition.status not in allowed_statuses[review.status]:
            raise ValueError(
                "The Reckoning agent disposition contradicts the bounded run status."
            )
        evidence_ids = {
            evidence.id
            for evidence in (
                review.worker_result.evidence if review.worker_result is not None else ()
            )
        }
        verified_evidence_ids = (
            review.verified_evidence_ids
            if review.verified_evidence_ids
            else disposition.verified_evidence_ids
        )
        unknown_evidence = set(verified_evidence_ids) - evidence_ids
        if unknown_evidence:
            raise ValueError(
                "The Reckoning agent verified evidence the worker did not return."
            )
        if (
            disposition.status == "accepted"
            and set(verified_evidence_ids) != evidence_ids
        ):
            raise ValueError(
                "The Reckoning agent must verify all worker evidence before acceptance."
            )


def _required(value: str, label: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{label} cannot be empty.")
    return stripped


def _unique(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} cannot contain duplicates.")


def _is_owner_tool(tool: str) -> bool:
    normalized = tool.casefold().replace("-", "_").replace(".", "_")
    return normalized in {
        "memory_write",
        "memory_confirm",
        "permission_broaden",
        "permission_grant",
        "grant_permission",
        "reply_to_user",
        "user_reply",
    }


def _receipt_to_data(receipt: DelegationReceipt) -> dict[str, object]:
    return {
        "id": receipt.id,
        "started_at": receipt.started_at.isoformat(),
        "purpose": receipt.purpose,
        "context_categories": list(receipt.context_categories),
        "provider": receipt.provider,
        "tools_allowed": list(receipt.tools_allowed),
        "tools_used": list(receipt.tools_used),
        "model_calls": receipt.model_calls,
        "cost": str(receipt.cost),
        "retries": receipt.retries,
        "result": receipt.result,
        "rationale": receipt.rationale,
        "verified_evidence_ids": list(receipt.verified_evidence_ids),
        "policy_violations": list(receipt.policy_violations),
        "tool_calls": receipt.tool_calls,
    }


def _receipt_from_data(data: dict[str, Any]) -> DelegationReceipt:
    return DelegationReceipt(
        id=str(data["id"]),
        started_at=datetime.fromisoformat(str(data["started_at"])),
        purpose=str(data["purpose"]),
        context_categories=tuple(str(item) for item in data["context_categories"]),
        provider=str(data["provider"]),
        tools_allowed=tuple(str(item) for item in data["tools_allowed"]),
        tools_used=tuple(str(item) for item in data["tools_used"]),
        model_calls=int(data["model_calls"]),
        cost=Decimal(str(data["cost"])),
        retries=int(data["retries"]),
        result=cast(DispositionStatus, data["result"]),
        rationale=str(data["rationale"]),
        verified_evidence_ids=tuple(
            str(item) for item in data["verified_evidence_ids"]
        ),
        policy_violations=tuple(str(item) for item in data["policy_violations"]),
        tool_calls=int(data.get("tool_calls", len(data["tools_used"]))),
    )
