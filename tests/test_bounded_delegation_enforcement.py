from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from reckoning.delegation import (
    AgentDisposition,
    BoundedWorkerRuntime,
    DelegatedContextEntry,
    DelegatedContextRequest,
    DelegatedModelResponse,
    DelegatedWorkerFinding,
    DelegationCoordinator,
    DelegationEvidence,
    DelegationPolicy,
    DelegationRequest,
    InMemoryDelegationReceiptRepository,
)


class ExactContextSource:
    def __init__(self) -> None:
        self.requested: list[tuple[str, tuple[str, ...]]] = []
        self.values = {
            "goal-1": "Ship the smallest verified slice.",
            "goal-2": "Unrelated and private.",
        }

    def read_entries(
        self, category: str, entry_ids: tuple[str, ...]
    ) -> tuple[DelegatedContextEntry, ...]:
        self.requested.append((category, entry_ids))
        return tuple(
            DelegatedContextEntry(entry_id, self.values[entry_id])
            for entry_id in entry_ids
        )


class ModelGateway:
    def __init__(self, quote: Decimal = Decimal("0.12")) -> None:
        self.quote = quote
        self.calls: list[tuple[str, str]] = []

    def quote_max_cost(self, provider: str, prompt: str) -> Decimal:
        return self.quote

    def execute(self, provider: str, prompt: str) -> DelegatedModelResponse:
        self.calls.append((provider, prompt))
        return DelegatedModelResponse("candidate", self.quote)


class ToolGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def execute(self, tool: str, payload: str) -> str:
        self.calls.append((tool, payload))
        return "official source"


class SimonReviewer:
    def __init__(
        self,
        disposition: AgentDisposition,
        verified: frozenset[str] = frozenset(),
    ) -> None:
        self.disposition = disposition
        self.verified = verified
        self.checked: list[str] = []
        self.reviews: list[Any] = []

    def verify_evidence(self, evidence: DelegationEvidence) -> bool:
        self.checked.append(evidence.id)
        return evidence.id in self.verified

    def dispose(self, review: Any) -> AgentDisposition:
        self.reviews.append(review)
        return self.disposition


def bounded_policy(**changes: object) -> DelegationPolicy:
    values: dict[str, object] = {
        "mode": "budget_bounded_delegation",
        "allowed_context_categories": frozenset({"active_goal"}),
        "allowed_tools": frozenset({"web_read"}),
        "allowed_providers": frozenset({"fake-worker"}),
        "max_model_calls": 1,
        "max_cost": Decimal("0.25"),
        "max_tool_calls": 1,
        "max_retries": 0,
    }
    values.update(changes)
    return DelegationPolicy(**values)


def bounded_request(**changes: object) -> DelegationRequest:
    values: dict[str, object] = {
        "purpose": "Check one official requirement",
        "instructions": "Return sourced evidence, not a user-facing answer.",
        "context_categories": ("active_goal",),
        "context": (DelegatedContextRequest("active_goal", ("goal-1",)),),
        "tools": ("web_read",),
        "provider": "fake-worker",
        "model_call_limit": 1,
        "cost_limit": Decimal("0.25"),
        "tool_call_limit": 1,
        "retry_limit": 0,
    }
    values.update(changes)
    return DelegationRequest(**values)


def run(
    worker: Any,
    reviewer: SimonReviewer,
    *,
    context: ExactContextSource | None = None,
    model: ModelGateway | None = None,
    tools: ToolGateway | None = None,
):
    context = context or ExactContextSource()
    model = model or ModelGateway()
    tools = tools or ToolGateway()
    outcome = DelegationCoordinator(
        context,
        worker,
        reviewer,
        InMemoryDelegationReceiptRepository(),
        model_gateway=model,
        tool_gateway=tools,
    ).delegate(
        bounded_request(),
        bounded_policy(),
        receipt_id="delegation-enforced",
        started_at=datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc),
    )
    return outcome, context, model, tools


def test_exact_context_and_host_measured_receipt() -> None:
    class Worker:
        def execute(
            self, task: Any, runtime: BoundedWorkerRuntime
        ) -> DelegatedWorkerFinding:
            assert [entry.id for entry in task.context[0].entries] == ["goal-1"]
            runtime.call_model("check")
            runtime.use_tool("web_read", "official")
            return DelegatedWorkerFinding(
                "candidate only",
                (DelegationEvidence("evidence-1", "claim", "official"),),
            )

    reviewer = SimonReviewer(
        AgentDisposition("accepted", "Simon owns the answer.", "Verified."),
        frozenset({"evidence-1"}),
    )
    outcome, context, _, _ = run(Worker(), reviewer)

    assert context.requested == [("active_goal", ("goal-1",))]
    assert outcome.response == "Simon owns the answer."
    assert "candidate only" not in outcome.response
    assert reviewer.checked == ["evidence-1"]
    assert outcome.receipt.context_categories == ("active_goal",)
    assert outcome.receipt.provider == "fake-worker"
    assert outcome.receipt.tools_allowed == ("web_read",)
    assert outcome.receipt.tools_used == ("web_read",)
    assert outcome.receipt.tool_calls == 1
    assert outcome.receipt.model_calls == 1
    assert outcome.receipt.cost == Decimal("0.12")
    assert outcome.receipt.result == "accepted"


@pytest.mark.parametrize(
    ("attempt", "violation"),
    [
        ("model", "model-call limit"),
        ("cost", "cost limit"),
        ("tool_count", "tool-call limit"),
        ("tool_scope", "delegated tool scope"),
        ("retry", "retry limit"),
    ],
)
def test_host_stops_overrun_before_external_call(
    attempt: str, violation: str
) -> None:
    class Worker:
        def execute(
            self, task: Any, runtime: BoundedWorkerRuntime
        ) -> DelegatedWorkerFinding:
            if attempt == "model":
                runtime.call_model("first")
                runtime.call_model("blocked")
            elif attempt == "cost":
                runtime.call_model("blocked")
            elif attempt == "tool_count":
                runtime.use_tool("web_read", "first")
                runtime.use_tool("web_read", "blocked")
            elif attempt == "tool_scope":
                runtime.use_tool("shell", "blocked")
            else:
                runtime.record_retry()
            raise AssertionError("limit attempt must not return")

    model = ModelGateway(Decimal("0.30") if attempt == "cost" else Decimal("0.12"))
    tools = ToolGateway()
    reviewer = SimonReviewer(
        AgentDisposition("limited", "Simon stopped the worker.", "Over limit.")
    )
    outcome, _, model, tools = run(
        Worker(), reviewer, model=model, tools=tools
    )

    assert any(violation in item for item in outcome.receipt.policy_violations)
    assert model.calls == (
        [("fake-worker", "first")] if attempt == "model" else []
    )
    assert tools.calls == (
        [("web_read", "first")] if attempt == "tool_count" else []
    )


@pytest.mark.parametrize(
    "authority_tool",
    [
        "memory_write",
        "memory.confirm",
        "permission_broaden",
        "grant-permission",
        "reply_to_user",
    ],
)
def test_worker_cannot_receive_owner_authority_tool(authority_tool: str) -> None:
    with pytest.raises(ValueError, match="reserved for Simon"):
        bounded_policy(allowed_tools=frozenset({authority_tool}))


def test_unverified_evidence_forces_limited_disposition() -> None:
    class Worker:
        def execute(
            self, task: Any, runtime: BoundedWorkerRuntime
        ) -> DelegatedWorkerFinding:
            return DelegatedWorkerFinding(
                "unsupported",
                (DelegationEvidence("evidence-1", "claim", "unreachable"),),
            )

    reviewer = SimonReviewer(
        AgentDisposition("limited", "Simon could not verify it.", "Unverified.")
    )
    outcome, _, _, _ = run(Worker(), reviewer)

    assert outcome.receipt.verified_evidence_ids == ()
    assert outcome.receipt.policy_violations == (
        "Simon could not verify worker evidence: evidence-1",
    )
    assert outcome.receipt.result == "limited"
