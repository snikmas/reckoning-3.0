from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from reckoning.automation import (
    BriefingFinding,
    BriefingPolicy,
    BriefingService,
    JsonFileAutomationRepository,
    RoutineContractDraft,
    RoutineService,
    RoutineStep,
    StepResult,
    WatchFinding,
    WatchService,
)


NOW = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)


def routine_contract(service: RoutineService, *, proposal_id: str = "routine-v1"):
    return service.propose(
        proposal_id=proposal_id,
        routine_id="weekly-goal-check",
        source_request="Every Sunday, check my approved goal sources and brief me.",
        created_at=NOW,
        trigger="0 18 * * SUN",
        source_scope=("official-goal-source",),
        context_scope=("goal:ielts",),
        tools=("read-official-source", "deliver-private-briefing"),
        permissions=("read:official-goal-source", "deliver:private-web"),
        delivery="private-web",
        model_policy="one synthesis call when findings exist",
        cost_ceiling=5,
        retry_limit=1,
        delegation_policy="direct execution only",
        failure_behavior="record failure and wait for the next trigger",
    )


def test_recurring_request_requires_confirmation_and_material_change_is_new_proposal(
    tmp_path: Path,
) -> None:
    service = RoutineService(JsonFileAutomationRepository(tmp_path / "automation.json"))
    proposal = routine_contract(service)

    with pytest.raises(PermissionError, match="confirmed"):
        service.start_run(
            run_id="run-before-confirmation",
            proposal_id=proposal.id,
            scheduled_for=NOW,
            idempotency_key="weekly-goal-check:2026-08-31",
            steps=(RoutineStep("read", "deterministic", "read-official-source"),),
            triggered_by="0 18 * * SUN",
        )

    confirmed = service.confirm(proposal.id)
    revision = service.revise(
        confirmed.id,
        proposal_id="routine-v2",
        created_at=NOW,
        changes={"source_scope": ("official-goal-source", "exam-board")},
    )

    assert confirmed.status == "confirmed"
    assert revision.status == "proposed"
    assert revision.supersedes_proposal_id == confirmed.id
    assert service.inspect_proposal(confirmed.id).status == "confirmed"
    assert revision.source_scope == ("official-goal-source", "exam-board")

    with pytest.raises(ValueError, match="trigger"):
        service.revise(
            confirmed.id,
            proposal_id="routine-invalid",
            created_at=NOW,
            changes={"trigger": ""},
        )


def test_natural_language_request_is_compiled_into_a_reviewable_contract(
    tmp_path: Path,
) -> None:
    service = RoutineService(JsonFileAutomationRepository(tmp_path / "compiled.json"))

    class Compiler:
        def compile(self, request: str) -> RoutineContractDraft:
            assert request == "Every Sunday, check my approved goal sources."
            return RoutineContractDraft(
                trigger="Sunday 18:00",
                source_scope=("official-goal-source",),
                context_scope=("goal:ielts",),
                tools=("read-official-source",),
                permissions=("read:official-goal-source",),
                delivery="private-web",
                model_policy="no model",
                cost_ceiling=0,
                retry_limit=1,
                delegation_policy="direct execution only",
                failure_behavior="record failure",
            )

    proposal = service.compile_request(
        "Every Sunday, check my approved goal sources.",
        compiler=Compiler(),
        proposal_id="compiled-v1",
        routine_id="compiled",
        created_at=NOW,
    )

    assert proposal.status == "proposed"
    assert proposal.trigger == "Sunday 18:00"
    assert proposal.tools == ("read-official-source",)

def test_confirmed_routine_resumes_with_idempotency_and_deterministic_step_uses_no_model(
    tmp_path: Path,
) -> None:
    path = tmp_path / "automation.json"
    first = RoutineService(JsonFileAutomationRepository(path))
    confirmed = first.confirm(routine_contract(first).id)
    first.start_run(
        run_id="run-1",
        proposal_id=confirmed.id,
        scheduled_for=NOW,
        idempotency_key="weekly-goal-check:2026-08-31",
        steps=(RoutineStep("deliver", "deterministic", "deliver-private-briefing"),),
        triggered_by="0 18 * * SUN",
    )
    effects: dict[str, str] = {}

    class InterruptedAfterEffect:
        def execute(self, step: RoutineStep, idempotency_key: str) -> StepResult:
            effects.setdefault(idempotency_key, "delivered once")
            raise InterruptedError("process stopped after the external response")

    with pytest.raises(InterruptedError):
        first.resume_run(
            "run-1",
            deterministic_executor=InterruptedAfterEffect(),
            model_executor=None,
            completed_at=NOW,
        )

    class IdempotentExecutor:
        def execute(self, step: RoutineStep, idempotency_key: str) -> StepResult:
            assert effects[idempotency_key] == "delivered once"
            return StepResult("success", effects[idempotency_key], cost_units=0)

    restarted = RoutineService(JsonFileAutomationRepository(path))
    receipt = restarted.resume_run(
        "run-1",
        deterministic_executor=IdempotentExecutor(),
        model_executor=None,
        completed_at=NOW,
    )

    assert receipt.status == "success"
    assert receipt.executed is True
    assert receipt.attempts == 2
    assert tuple(effects.values()) == ("delivered once",)
    assert restarted.inspect_run("run-1").status == "success"

    no_run = restarted.record_no_run(
        proposal_id=confirmed.id,
        evaluated_at=NOW,
        reason="trigger did not fire",
    )
    assert no_run.status == "no_run"
    assert no_run.executed is False


def test_watch_suppresses_weak_changes_and_can_propose_relevant_check_in() -> None:
    service = WatchService()
    proposal = service.propose(
        watch_id="ielts-watch",
        source_request_id="research-1",
        trigger="daily at 08:00",
        source_scope=("official-exam-board",),
        notification_threshold=0.75,
        budget=4,
        delivery_policy="private-web",
        open_ended=True,
    )
    with pytest.raises(PermissionError, match="confirmed"):
        service.run(
            proposal.id,
            run_id="watch-run-0",
            triggered_by="daily at 08:00",
            quoted_cost_units=0,
            findings=(),
            checked_at=NOW,
        )

    service.confirm(proposal.id)
    with pytest.raises(PermissionError, match="trigger"):
        service.run(
            proposal.id,
            run_id="watch-run-wrong-trigger",
            triggered_by="manual research",
            quoted_cost_units=0,
            findings=(),
            checked_at=NOW,
        )
    receipt = service.run(
        proposal.id,
        run_id="watch-run-1",
        triggered_by="daily at 08:00",
        quoted_cost_units=3,
        checked_at=NOW,
        findings=(
            WatchFinding(
                "material",
                "official-exam-board",
                "goal:ielts",
                "The official score requirement increased.",
                credibility=0.95,
                relevance=0.9,
                cost_units=2,
                evidence="Dated official requirement page",
            ),
            WatchFinding(
                "weak",
                "official-exam-board",
                "goal:ielts",
                "An unsourced forum rumor repeats the old requirement.",
                credibility=0.2,
                relevance=0.8,
                cost_units=1,
                evidence="Anonymous comment",
            ),
        ),
    )

    assert [item.id for item in receipt.notified] == ["material"]
    assert [item.id for item in receipt.suppressed] == ["weak"]
    assert receipt.check_in_proposed is True
    assert receipt.total_cost_units == 3


def test_routine_failure_uses_bounded_retries_and_truthful_terminal_receipt(
    tmp_path: Path,
) -> None:
    service = RoutineService(JsonFileAutomationRepository(tmp_path / "automation.json"))
    confirmed = service.confirm(routine_contract(service).id)
    service.start_run(
        run_id="failed-run",
        proposal_id=confirmed.id,
        scheduled_for=NOW,
        idempotency_key="failed-run-key",
        steps=(RoutineStep("read", "deterministic", "read-official-source"),),
        triggered_by="0 18 * * SUN",
    )

    class OfflineExecutor:
        def execute(self, step: RoutineStep, idempotency_key: str) -> StepResult:
            raise OSError("source is offline")

    receipt = service.resume_run(
        "failed-run",
        deterministic_executor=OfflineExecutor(),
        model_executor=None,
        completed_at=NOW,
    )

    assert receipt.status == "failed"
    assert receipt.attempts == 2
    assert receipt.results[0].detail == "source is offline"


def test_routine_blocks_quoted_cost_before_executor_side_effect(tmp_path: Path) -> None:
    service = RoutineService(JsonFileAutomationRepository(tmp_path / "cost.json"))
    proposal = service.propose(
        proposal_id="cost-v1",
        routine_id="cost",
        source_request="Summarize one approved source.",
        created_at=NOW,
        trigger="manual",
        source_scope=("approved-source",),
        context_scope=("goal:one",),
        tools=("summarize",),
        permissions=("read:approved-source",),
        delivery="private-web",
        model_policy="one model call",
        cost_ceiling=2,
        retry_limit=0,
        delegation_policy="direct",
        failure_behavior="record blocked receipt",
    )
    service.confirm(proposal.id)
    service.start_run(
        run_id="cost-run",
        proposal_id=proposal.id,
        scheduled_for=NOW,
        idempotency_key="cost-run",
        steps=(RoutineStep("summary", "model", "summarize"),),
        triggered_by="manual",
    )

    class ExpensiveExecutor:
        called = False

        def quote_cost(self, step: RoutineStep) -> int:
            return 3

        def execute(self, step: RoutineStep, idempotency_key: str) -> StepResult:
            self.called = True
            return StepResult("success", "should not run", cost_units=3)

    executor = ExpensiveExecutor()
    receipt = service.resume_run(
        "cost-run",
        deterministic_executor=executor,
        model_executor=executor,
        completed_at=NOW,
    )

    assert receipt.status == "blocked"
    assert executor.called is False


def test_routine_never_claims_success_when_executor_exceeds_quote(
    tmp_path: Path,
) -> None:
    service = RoutineService(JsonFileAutomationRepository(tmp_path / "quote.json"))
    proposal = service.propose(
        proposal_id="quote-v1",
        routine_id="quote",
        source_request="Summarize one source.",
        created_at=NOW,
        trigger="manual",
        source_scope=("source",),
        context_scope=("goal",),
        tools=("summarize",),
        permissions=("read:source",),
        delivery="private-web",
        model_policy="one model call",
        cost_ceiling=2,
        retry_limit=0,
        delegation_policy="direct",
        failure_behavior="record blocked receipt",
    )
    service.confirm(proposal.id)
    service.start_run(
        run_id="quote-run",
        proposal_id=proposal.id,
        scheduled_for=NOW,
        idempotency_key="quote-run",
        steps=(RoutineStep("summary", "model", "summarize"),),
        triggered_by="manual",
    )

    class QuoteBreakingExecutor:
        def quote_cost(self, step: RoutineStep) -> int:
            return 1

        def execute(self, step: RoutineStep, idempotency_key: str) -> StepResult:
            return StepResult("success", "adapter breached quote", cost_units=100)

    executor = QuoteBreakingExecutor()
    receipt = service.resume_run(
        "quote-run",
        deterministic_executor=executor,
        model_executor=executor,
        completed_at=NOW,
    )

    assert receipt.status == "blocked"
    assert receipt.results[0].cost_units == 100
    assert "exceeded" in receipt.results[0].detail


def test_selective_briefing_ranks_within_cap_and_truthfully_reports_no_change() -> None:
    service = BriefingService()
    policy = BriefingPolicy(max_items=1, max_cost_units=3, delivery_preference="private-web")
    lower = BriefingFinding(
        "lower",
        "Secondary background",
        goal_relevance=0.5,
        evidence_quality=0.7,
        novelty=0.4,
        urgency=0.3,
        cost_units=1,
        attention_burden=0.3,
        delivery="private-web",
    )
    higher = BriefingFinding(
        "higher",
        "Official deadline changed",
        goal_relevance=1.0,
        evidence_quality=1.0,
        novelty=0.9,
        urgency=0.9,
        cost_units=2,
        attention_burden=0.2,
        delivery="private-web",
    )

    receipt = service.deliver(
        run_id="briefing-1", policy=policy, findings=(lower, higher), delivered_at=NOW
    )
    assert [item.finding.id for item in receipt.items] == ["higher"]
    assert all(item.explanation for item in receipt.items)
    assert receipt.total_cost_units == 2

    empty = service.deliver(
        run_id="briefing-2", policy=policy, findings=(), delivered_at=NOW
    )
    assert empty.status == "success"
    assert empty.no_material_change is True
    assert empty.items == ()
    assert empty.executed is True
