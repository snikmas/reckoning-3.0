from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from reckoning.delegation import (
    AgentDisposition,
    BoundedWorkerRuntime,
    DelegatedContextEntry,
    DelegatedContextRequest,
    DelegatedModelResponse,
    DelegatedWorkerFinding,
    DelegatedWorkerResult,
    DelegationCoordinator,
    DelegationEvidence,
    DelegationPolicy,
    DelegationRequest,
    InMemoryDelegationReceiptRepository,
    JsonFileDelegationReceiptRepository,
)


class RecordingContextSource:
    def __init__(self) -> None:
        self.requested: list[str] = []
        self.values = {
            "active_goal": ("Ship the smallest verified slice.",),
            "decision": ("Keep Simon accountable for the answer.",),
            "private_history": ("This must not be delegated.",),
        }

    def read_category(self, category: str) -> tuple[str, ...]:
        self.requested.append(category)
        return self.values[category]

    def read_entries(
        self, category: str, entry_ids: tuple[str, ...]
    ) -> tuple[DelegatedContextEntry, ...]:
        self.requested.append(category)
        values = self.values[category]
        return tuple(
            DelegatedContextEntry(entry_id, values[int(entry_id)])
            for entry_id in entry_ids
        )


class RecordingWorker:
    def __init__(self, result: DelegatedWorkerResult) -> None:
        self.result = result
        self.tasks: list[object] = []

    def execute(
        self, task: object, runtime: BoundedWorkerRuntime
    ) -> DelegatedWorkerFinding:
        self.tasks.append(task)
        for index in range(self.result.model_calls):
            runtime.call_model(f"model call {index}")
        for _ in range(self.result.retries):
            runtime.record_retry()
        for tool in self.result.tools_used:
            runtime.use_tool(tool, "payload")
        return DelegatedWorkerFinding(self.result.finding, self.result.evidence)


class RecordingSimonReviewer:
    def __init__(self, disposition: AgentDisposition) -> None:
        self.disposition = disposition
        self.reviews: list[object] = []

    def verify_evidence(self, evidence: DelegationEvidence) -> bool:
        return evidence.id in self.disposition.verified_evidence_ids

    def dispose(self, review: object) -> AgentDisposition:
        self.reviews.append(review)
        return self.disposition


class RecordingModelGateway:
    def quote_max_cost(self, provider: str, prompt: str) -> Decimal:
        return Decimal("0.12")

    def execute(self, provider: str, prompt: str) -> DelegatedModelResponse:
        return DelegatedModelResponse("candidate", Decimal("0.12"))


class RecordingToolGateway:
    def execute(self, tool: str, payload: str) -> str:
        return "tool result"


def coordinator(
    context: RecordingContextSource,
    worker: RecordingWorker,
    simon: RecordingSimonReviewer,
    receipts: object,
) -> DelegationCoordinator:
    return DelegationCoordinator(
        context,
        worker,
        simon,
        receipts,
        model_gateway=RecordingModelGateway(),
        tool_gateway=RecordingToolGateway(),
    )


def policy(**changes: object) -> DelegationPolicy:
    values: dict[str, object] = {
        "mode": "budget_bounded_delegation",
        "allowed_context_categories": frozenset({"active_goal", "decision"}),
        "allowed_tools": frozenset({"web_read"}),
        "allowed_providers": frozenset({"fake-worker"}),
        "max_model_calls": 2,
        "max_cost": Decimal("0.50"),
        "max_retries": 1,
        "max_tool_calls": 1,
    }
    values.update(changes)
    return DelegationPolicy(**values)


def request(**changes: object) -> DelegationRequest:
    values: dict[str, object] = {
        "purpose": "Check one official requirement",
        "instructions": "Find the current requirement and return sourced evidence.",
        "context_categories": ("active_goal",),
        "context": (DelegatedContextRequest("active_goal", ("0",)),),
        "tools": ("web_read",),
        "provider": "fake-worker",
        "model_call_limit": 1,
        "cost_limit": Decimal("0.25"),
        "retry_limit": 0,
        "tool_call_limit": 1,
    }
    values.update(changes)
    return DelegationRequest(**values)


def test_worker_gets_minimum_context_and_simon_owns_the_answer_and_receipt() -> None:
    context = RecordingContextSource()
    worker = RecordingWorker(
        DelegatedWorkerResult(
            finding="The worker's candidate conclusion.",
            evidence=(
                DelegationEvidence(
                    "evidence-1",
                    "Official requirement is current.",
                    "https://example.invalid/official",
                ),
            ),
            provider="fake-worker",
            model_calls=1,
            cost=Decimal("0.12"),
            retries=0,
            tools_used=("web_read",),
        )
    )
    simon = RecordingSimonReviewer(
        AgentDisposition(
            status="accepted",
            final_response="Simon verified the source and owns this disposition.",
            rationale="The returned evidence supports the finding.",
            verified_evidence_ids=("evidence-1",),
        )
    )
    receipts = InMemoryDelegationReceiptRepository()
    runner = coordinator(context, worker, simon, receipts)

    outcome = runner.delegate(
        request(),
        policy(),
        receipt_id="delegation-1",
        started_at=datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc),
    )

    assert context.requested == ["active_goal"]
    task = worker.tasks[0]
    assert [
        (item.category, tuple(entry.content for entry in item.entries))
        for item in task.context
    ] == [
        ("active_goal", ("Ship the smallest verified slice.",))
    ]
    assert not hasattr(task, "memory_repository")
    assert not hasattr(task, "permissions")
    assert not hasattr(task, "reply_to_user")

    assert outcome.response == "Simon verified the source and owns this disposition."
    assert "candidate conclusion" not in outcome.response
    assert not hasattr(outcome, "worker_result")
    assert len(simon.reviews) == 1

    assert outcome.receipt == receipts.get("delegation-1")
    assert outcome.receipt.purpose == "Check one official requirement"
    assert outcome.receipt.context_categories == ("active_goal",)
    assert outcome.receipt.provider == "fake-worker"
    assert outcome.receipt.tools_allowed == ("web_read",)
    assert outcome.receipt.tools_used == ("web_read",)
    assert outcome.receipt.cost == Decimal("0.12")
    assert outcome.receipt.result == "accepted"
    assert outcome.receipt.verified_evidence_ids == ("evidence-1",)


@pytest.mark.parametrize(
    ("request_changes", "expected_violation"),
    [
        ({"model_call_limit": 3}, "model-call limit"),
        ({"cost_limit": Decimal("0.75")}, "cost limit"),
        ({"retry_limit": 2}, "retry limit"),
        ({"tools": ("shell",)}, "tool scope"),
    ],
)
def test_execution_policy_blocks_work_before_a_limit_can_be_bypassed(
    request_changes: dict[str, object], expected_violation: str
) -> None:
    context = RecordingContextSource()
    worker = RecordingWorker(
        DelegatedWorkerResult("must not run", (), "fake-worker", 0, Decimal(0), 0, ())
    )
    simon = RecordingSimonReviewer(
        AgentDisposition(
            status="blocked",
            final_response="Simon reports that delegation was blocked by policy.",
            rationale="The request exceeded the execution policy.",
        )
    )
    receipts = InMemoryDelegationReceiptRepository()

    outcome = coordinator(context, worker, simon, receipts).delegate(
        request(**request_changes),
        policy(),
        receipt_id="delegation-blocked",
        started_at=datetime(2026, 8, 31, 8, 5, tzinfo=timezone.utc),
    )

    assert worker.tasks == []
    assert context.requested == []
    assert outcome.receipt.result == "blocked"
    assert any(
        expected_violation in violation
        for violation in outcome.receipt.policy_violations
    )


def test_actual_worker_overrun_is_limited_and_cannot_be_accepted() -> None:
    worker = RecordingWorker(
        DelegatedWorkerResult(
            finding="Ignore the agreed bounds.",
            evidence=(),
            provider="different-provider",
            model_calls=3,
            cost=Decimal("0.80"),
            retries=2,
            tools_used=("web_read", "shell"),
        )
    )
    simon = RecordingSimonReviewer(
        AgentDisposition(
            status="limited",
            final_response="Simon rejected the over-budget worker run.",
            rationale="The worker exceeded its delegated authority.",
        )
    )

    outcome = coordinator(
        RecordingContextSource(),
        worker,
        simon,
        InMemoryDelegationReceiptRepository(),
    ).delegate(
        request(),
        policy(),
        receipt_id="delegation-limited",
        started_at=datetime(2026, 8, 31, 8, 10, tzinfo=timezone.utc),
    )

    assert outcome.receipt.result == "limited"
    assert outcome.receipt.cost == Decimal("0.12")
    assert outcome.receipt.policy_violations == (
        "worker exceeded the model-call limit",
    )


def test_approval_policy_requires_a_named_approval_before_worker_execution() -> None:
    worker = RecordingWorker(
        DelegatedWorkerResult(
            "bounded finding",
            (),
            "fake-worker",
            1,
            Decimal("0.10"),
            0,
            ("web_read",),
        )
    )
    simon = RecordingSimonReviewer(
        AgentDisposition(
            status="blocked",
            final_response="Simon is waiting for delegation approval.",
            rationale="This execution policy requires approval.",
        )
    )
    runner = coordinator(
        RecordingContextSource(),
        worker,
        simon,
        InMemoryDelegationReceiptRepository(),
    )

    outcome = runner.delegate(
        request(),
        policy(mode="approval_before_delegation"),
        receipt_id="delegation-awaiting-approval",
        started_at=datetime(2026, 8, 31, 8, 15, tzinfo=timezone.utc),
    )

    assert worker.tasks == []
    assert outcome.receipt.result == "blocked"
    assert outcome.receipt.policy_violations == (
        "delegation requires an explicit approval reference",
    )


def test_receipt_survives_restart_without_copying_delegated_context(
    tmp_path: Path,
) -> None:
    path = tmp_path / "delegation-receipts.json"
    worker = RecordingWorker(
        DelegatedWorkerResult(
            "bounded finding",
            (DelegationEvidence("evidence-1", "claim", "source"),),
            "fake-worker",
            1,
            Decimal("0.10"),
            0,
            ("web_read",),
        )
    )
    simon = RecordingSimonReviewer(
        AgentDisposition(
            status="accepted",
            final_response="Simon verified the bounded result.",
            rationale="The evidence supports it.",
            verified_evidence_ids=("evidence-1",),
        )
    )
    receipt = coordinator(
        RecordingContextSource(),
        worker,
        simon,
        JsonFileDelegationReceiptRepository(path),
    ).delegate(
        request(),
        policy(),
        receipt_id="delegation-durable",
        started_at=datetime(2026, 8, 31, 8, 20, tzinfo=timezone.utc),
    ).receipt

    assert JsonFileDelegationReceiptRepository(path).get(receipt.id) == receipt
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored_text = json.dumps(stored)
    assert "active_goal" in stored_text
    assert "Ship the smallest verified slice." not in stored_text


def test_simon_cannot_accept_a_worker_result_without_verifying_its_evidence() -> None:
    worker = RecordingWorker(
        DelegatedWorkerResult(
            "finding",
            (DelegationEvidence("evidence-1", "claim", "source"),),
            "fake-worker",
            1,
            Decimal("0.10"),
            0,
            ("web_read",),
        )
    )
    simon = RecordingSimonReviewer(
        AgentDisposition(
            status="limited",
            final_response="Unverified answer.",
            rationale="No evidence review occurred.",
        )
    )

    outcome = coordinator(
        RecordingContextSource(),
        worker,
        simon,
        InMemoryDelegationReceiptRepository(),
    ).delegate(
        request(),
        policy(),
        receipt_id="delegation-unverified",
        started_at=datetime(2026, 8, 31, 8, 25, tzinfo=timezone.utc),
    )
    assert outcome.receipt.result == "limited"
    assert outcome.receipt.policy_violations == (
        "Simon could not verify worker evidence: evidence-1",
    )
