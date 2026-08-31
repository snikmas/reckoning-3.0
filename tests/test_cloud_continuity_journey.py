from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from reckoning.application import (
    ApplicationDependencies,
    InMemoryConversationStorage,
    NoConnectors,
    PlacementState,
    ReckoningApplication,
)
from reckoning.persistence import JsonFileReckoningRepository
from reckoning.providers import OrcaRouterModelProvider, OrcaRouterReckoningProvider


class AdvancingClock:
    def __init__(self, instant: datetime) -> None:
        self.instant = instant

    def now(self) -> datetime:
        current = self.instant
        self.instant += timedelta(seconds=1)
        return current


class SequenceIdentifiers:
    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def new(self) -> str:
        return next(self._values)


def test_orcarouter_drives_first_reckoning_then_durable_journey_continues(
    tmp_path: Path,
) -> None:
    payload = {
        "conflict": "Protect the exam baseline or maximize project momentum.",
        "questions": [
            {
                "text": "When is the exam?",
                "effect_on_recommendation": "A near exam makes preparation primary.",
            }
        ],
        "matters_now": ["Protect the exam baseline."],
        "maintained": ["Keep one project block."],
        "parked": ["Park broad job applications."],
        "uncertainties": ["The latest practice score is missing."],
        "known": ["The exam is a fixed commitment."],
        "inferences": [
            {
                "text": "One project block may preserve momentum.",
                "uncertainty": "The block size is not yet tested.",
            }
        ],
        "next_step": "Schedule the first exam block.",
        "proposed_records": [
            {
                "record_type": "decision",
                "meaning": "Protect the exam and keep one project block.",
            }
        ],
    }

    def transport(request: object, timeout: float) -> bytes:
        request_payload = json.loads(request.data)
        assert request_payload["messages"][-1]["content"].startswith("I need to")
        return json.dumps(
            {
                "model": "orcarouter/auto",
                "choices": [{"message": {"content": json.dumps(payload)}}],
                "usage": {
                    "prompt_tokens": 200,
                    "completion_tokens": 100,
                    "total_tokens": 300,
                },
            }
        ).encode()

    model = OrcaRouterModelProvider("secret", transport=transport)
    repository_path = tmp_path / "continuity.json"
    clock = AdvancingClock(datetime(2026, 8, 30, 16, 0, tzinfo=timezone.utc))
    application = ReckoningApplication(
        ApplicationDependencies(
            clock=clock,
            model=model,
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            reckoning_provider=OrcaRouterReckoningProvider(model),
            reckoning_repository=JsonFileReckoningRepository(repository_path),
            identifiers=SequenceIdentifiers(
                "run-1",
                "reckoning-1",
                "record-1",
                "correction-evidence-1",
            ),
        )
    )

    reckoning = application.start_reckoning(
        "I need to protect an exam without abandoning this project."
    )
    corrected = application.correct_personal_record(
        reckoning.id,
        reckoning.current_records[0].record_id,
        "Protect the exam baseline while keeping one project block.",
    )
    application.confirm_reckoning(reckoning.id)

    run = application.inspect_model_runs()[0]
    assert run.status == "succeeded"
    assert run.provider == "orcarouter"
    assert run.billable_units == 300
    assert corrected.current_records[0].version == 2

    restarted = ReckoningApplication(
        ApplicationDependencies(
            clock=clock,
            model=model,
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            reckoning_repository=JsonFileReckoningRepository(repository_path),
            identifiers=SequenceIdentifiers("check-in-1"),
        )
    )
    resumed = restarted.resume_decision(reckoning.id)
    check_in = restarted.record_check_in(
        reckoning.id,
        "I completed the exam blocks and kept one project block.",
    )

    assert resumed.decision.status == "confirmed"
    assert resumed.stored_facts[-1].startswith("Protect the exam baseline")
    assert check_in.id == "check-in-1"
