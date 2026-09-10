from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from reckoning.json_store import atomic_write_json
from reckoning.operations import RELEASE_GATES, release_readiness
from reckoning.trials import (
    JsonFileTrialRepository,
    REQUIRED_REAL_USE_METRICS,
    TrialEvidence,
    TrialRecorder,
)


NOW = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)


def accepted_trial(path: Path) -> None:
    recorder = TrialRecorder(JsonFileTrialRepository(path))
    trial = recorder.start("trial-accepted", "first-slice", NOW, assessed_revision="r1")
    trial = recorder.record(
        trial.id,
        TrialEvidence(
            "safety",
            "automated_check",
            None,
            "passed",
            NOW,
            check_outcome="passed",
        ),
        expected_revision=trial.version,
    )
    for metric in REQUIRED_REAL_USE_METRICS:
        trial = recorder.record(
            trial.id,
            TrialEvidence(
                f"observation-{metric}",
                "real_use",
                metric,
                "positive",
                NOW,
                real_use_outcome="positive",
            ),
            expected_revision=trial.version,
        )
    recorder.decide(
        trial.id,
        "accepted",
        reason="The first user explicitly accepted this assessed revision.",
        decided_at=NOW,
        assessed_revision="r1",
        expected_revision=trial.version,
    )


def test_release_readiness_consumes_valid_trial_acceptance_and_other_gates(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "release-evidence.json"
    trial_path = tmp_path / "trials.json"
    payload = {"schema_version": 1, **{gate: True for gate in RELEASE_GATES}}
    atomic_write_json(evidence_path, payload)

    missing_explicit_acceptance = release_readiness(evidence_path)

    assert missing_explicit_acceptance.ready is False
    assert missing_explicit_acceptance.missing_gates == ("repeated_value",)

    accepted_trial(trial_path)
    payload["trial_acceptance"] = {
        "path": trial_path.name,
        "trial_id": "trial-accepted",
    }
    atomic_write_json(evidence_path, payload)

    ready = release_readiness(evidence_path)
    assert ready.ready is True
    assert ready.missing_gates == ()

    payload["privacy"] = False
    atomic_write_json(evidence_path, payload)
    independently_blocked = release_readiness(evidence_path)
    assert independently_blocked.missing_gates == ("privacy",)


def test_release_readiness_does_not_create_a_missing_trial_reference(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "release-evidence.json"
    missing_trial = tmp_path / "missing-trials.json"
    payload = {
        "schema_version": 1,
        **{gate: True for gate in RELEASE_GATES},
        "trial_acceptance": {
            "path": missing_trial.name,
            "trial_id": "missing",
        },
    }
    atomic_write_json(evidence_path, payload)

    readiness = release_readiness(evidence_path)

    assert readiness.missing_gates == ("repeated_value",)
    assert not missing_trial.exists()
