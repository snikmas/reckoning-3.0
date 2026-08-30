from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from reckoning.trials import (
    JsonFileTrialRepository,
    REQUIRED_REAL_USE_METRICS,
    TrialEvidence,
    TrialRecorder,
)


def test_slice_stays_unaccepted_until_automated_and_real_use_evidence_exist(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trials.json"
    instant = datetime(2026, 8, 30, 17, 0, tzinfo=timezone.utc)
    recorder = TrialRecorder(JsonFileTrialRepository(path))
    trial = recorder.start("trial-1", "continuity-with-deepseek", instant)

    trial = recorder.record(
        trial.id,
        TrialEvidence(
            "check-1",
            "automated_check",
            None,
            "11 provider and continuity tests passed",
            instant,
        ),
    )

    assert trial.accepted is False
    assert trial.missing_evidence == REQUIRED_REAL_USE_METRICS

    for index, metric in enumerate(REQUIRED_REAL_USE_METRICS, start=1):
        trial = recorder.record(
            trial.id,
            TrialEvidence(
                f"observation-{index}",
                "real_use",
                metric,
                f"Mary recorded {metric.replace('_', ' ')}.",
                instant,
            ),
        )

    assert trial.accepted is True
    assert trial.missing_evidence == ()

    restarted = JsonFileTrialRepository(path).get(trial.id)
    assert restarted == trial
    assert {item.kind for item in restarted.evidence} == {
        "automated_check",
        "real_use",
    }
