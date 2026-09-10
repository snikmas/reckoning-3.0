from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from reckoning.operations import create_transfer, restore_transfer
from reckoning.trials import (
    JsonFileTrialRepository,
    REQUIRED_REAL_USE_METRICS,
    TrialEvidence,
    TrialRecorder,
    TrialRevisionConflict,
)


NOW = datetime(2026, 8, 30, 17, 0, tzinfo=timezone.utc)


def add_complete_real_use(
    recorder: TrialRecorder, trial_id: str, revision: int, *, positive: bool = True
) -> int:
    for index, metric in enumerate(REQUIRED_REAL_USE_METRICS, start=1):
        trial = recorder.record(
            trial_id,
            TrialEvidence(
                f"observation-{index}",
                "real_use",
                metric,
                "positive" if positive else "negative",
                NOW,
                real_use_outcome="positive" if positive else "negative",
            ),
            expected_revision=revision,
        )
        revision = trial.version
    return revision


def test_complete_positive_evidence_remains_pending_until_explicit_acceptance(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trials.json"
    recorder = TrialRecorder(JsonFileTrialRepository(path))
    trial = recorder.start(
        "trial-1", "continuity-with-provider", NOW, assessed_revision="abc123"
    )
    trial = recorder.record(
        trial.id,
        TrialEvidence(
            "check-1",
            "automated_check",
            None,
            "Required checks passed.",
            NOW,
            check_outcome="passed",
        ),
        expected_revision=trial.version,
    )
    revision = add_complete_real_use(recorder, trial.id, trial.version)

    pending = JsonFileTrialRepository(path).get(trial.id)

    assert pending.evidence_complete is True
    assert pending.required_checks_pass is True
    assert pending.decision.status == "pending"
    assert pending.accepted is False

    accepted = recorder.decide(
        trial.id,
        "accepted",
        reason="The assessed revision met the trial contract.",
        decided_at=NOW + timedelta(days=1),
        assessed_revision="abc123",
        expected_revision=revision,
    )

    assert accepted.accepted is True
    assert JsonFileTrialRepository(path).get(trial.id) == accepted

    report = recorder.inspect(trial.id)
    assert report.trial_id == trial.id
    assert report.assessed_revision == "abc123"
    assert report.record_version == accepted.version
    assert report.decision.status == "accepted"
    assert report.decision.reason == "The assessed revision met the trial contract."
    assert report.decision.decided_at == NOW + timedelta(days=1)
    assert report.decision.assessed_revision == "abc123"
    assert report.total_evidence_count == 8
    assert report.current_evidence_count == 8
    assert report.missing_evidence == ()
    assert report.release_accepted is True


@pytest.mark.parametrize("negative_kind", ["failed-check", "negative-real-use"])
def test_complete_negative_evidence_cannot_be_accepted(
    tmp_path: Path, negative_kind: str
) -> None:
    recorder = TrialRecorder(JsonFileTrialRepository(tmp_path / "trials.json"))
    trial = recorder.start("trial-negative", "slice", NOW, assessed_revision="r1")
    trial = recorder.record(
        trial.id,
        TrialEvidence(
            "required-check",
            "automated_check",
            None,
            "The safety check failed." if negative_kind == "failed-check" else "passed",
            NOW,
            check_outcome="failed" if negative_kind == "failed-check" else "passed",
        ),
        expected_revision=trial.version,
    )
    revision = add_complete_real_use(
        recorder,
        trial.id,
        trial.version,
        positive=negative_kind != "negative-real-use",
    )

    if negative_kind == "failed-check":
        with pytest.raises(ValueError, match="Required trial checks"):
            recorder.decide(
                trial.id,
                "accepted",
                reason="Do not infer from prose.",
                decided_at=NOW,
                assessed_revision="r1",
                expected_revision=revision,
            )
    else:
        with pytest.raises(ValueError, match="real-use evidence is not positive"):
            recorder.decide(
                trial.id,
                "accepted",
                reason="Negative evidence cannot be accepted.",
                decided_at=NOW,
                assessed_revision="r1",
                expected_revision=revision,
            )
        rejected = recorder.decide(
            trial.id,
            "rejected",
            reason="The first user found the result unhelpful.",
            decided_at=NOW,
            assessed_revision="r1",
            expected_revision=revision,
        )
        assert rejected.evidence_complete is True
        assert rejected.accepted is False
        assert rejected.decision.status == "rejected"


def test_stale_acceptance_conflicts_and_material_change_requires_revalidation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trials.json"
    first = TrialRecorder(JsonFileTrialRepository(path))
    second = TrialRecorder(JsonFileTrialRepository(path))
    trial = first.start("trial-1", "slice", NOW, assessed_revision="r1")
    trial = first.record(
        trial.id,
        TrialEvidence(
            "required-check",
            "automated_check",
            None,
            "passed",
            NOW,
            check_outcome="passed",
        ),
        expected_revision=trial.version,
    )
    stale_revision = trial.version
    current_revision = add_complete_real_use(first, trial.id, trial.version)

    with pytest.raises(TrialRevisionConflict, match="changed from revision"):
        second.decide(
            trial.id,
            "accepted",
            reason="Stale decision.",
            decided_at=NOW,
            assessed_revision="r1",
            expected_revision=stale_revision,
        )

    accepted = first.decide(
        trial.id,
        "accepted",
        reason="Accepted r1.",
        decided_at=NOW,
        assessed_revision="r1",
        expected_revision=current_revision,
    )
    reassessed = first.reassess_revision(
        trial.id,
        "r2",
        reason="Material behavior changed.",
        changed_at=NOW + timedelta(days=1),
        expected_revision=accepted.version,
    )

    assert reassessed.accepted is False
    assert reassessed.decision.status == "pending"
    assert reassessed.current_evidence == ()
    assert [decision.status for decision in reassessed.decisions[-2:]] == [
        "accepted",
        "pending",
    ]
    with pytest.raises(ValueError, match="evidence is incomplete"):
        first.decide(
            trial.id,
            "accepted",
            reason="Old evidence must not accept a changed revision.",
            decided_at=NOW + timedelta(days=1),
            assessed_revision="r2",
            expected_revision=reassessed.version,
        )
    report = first.inspect(trial.id)
    assert report.total_evidence_count == 8
    assert report.current_evidence_count == 0


def test_legacy_complete_evidence_migrates_to_pending_without_inferred_pass(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trials.json"
    evidence = [
        {
            "id": "legacy-check",
            "kind": "automated_check",
            "metric": None,
            "value": "all checks passed",
            "observed_at": NOW.isoformat(),
            "notes": "original prose retained",
        }
    ] + [
        {
            "id": f"legacy-{metric}",
            "kind": "real_use",
            "metric": metric,
            "value": "positive",
            "observed_at": NOW.isoformat(),
            "notes": "",
        }
        for metric in REQUIRED_REAL_USE_METRICS
    ]
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "trials": [
                    {
                        "id": "legacy",
                        "slice_id": "old-slice",
                        "started_at": NOW.isoformat(),
                        "evidence": evidence,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    migrated = JsonFileTrialRepository(path).get("legacy")

    assert migrated.evidence_complete is True
    assert migrated.required_checks_pass is False
    assert migrated.decision.status == "pending"
    assert migrated.accepted is False
    assert migrated.evidence[0].notes == "original prose retained"
    assert json.loads(path.read_text(encoding="utf-8"))["authority"] == "sqlite"


def test_legacy_incomplete_evidence_also_migrates_to_pending(tmp_path: Path) -> None:
    path = tmp_path / "trials.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "trials": [
                    {
                        "id": "legacy-incomplete",
                        "slice_id": "old-slice",
                        "started_at": NOW.isoformat(),
                        "evidence": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    migrated = JsonFileTrialRepository(path).get("legacy-incomplete")

    assert migrated.evidence_complete is False
    assert migrated.decision.status == "pending"
    assert migrated.accepted is False


def test_ready_trial_store_refuses_to_overwrite_changed_legacy_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trials.json"
    original = {"schema_version": 1, "trials": []}
    path.write_text(json.dumps(original), encoding="utf-8")
    JsonFileTrialRepository(path)
    changed = {
        "schema_version": 1,
        "trials": [
            {
                "id": "unexpected",
                "slice_id": "changed-after-migration",
                "started_at": NOW.isoformat(),
                "evidence": [],
            }
        ],
    }
    path.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(RuntimeError, match="protected trial rollback source differs"):
        JsonFileTrialRepository(path)

    assert json.loads(path.read_text(encoding="utf-8")) == changed


def test_trial_state_survives_clean_backup_and_restore(tmp_path: Path) -> None:
    source = tmp_path / "source"
    restored = tmp_path / "restored"
    archive = tmp_path / "trials.reckoning"
    path = source / "trials.json"
    recorder = TrialRecorder(JsonFileTrialRepository(path))
    trial = recorder.start("trial-backup", "slice", NOW, assessed_revision="r1")

    create_transfer(
        source,
        archive,
        "correct-horse-battery-staple",
        kind="backup",
    )
    restore_transfer(
        archive,
        restored,
        "correct-horse-battery-staple",
    )

    recovered = JsonFileTrialRepository(restored / "trials.json").get(trial.id)
    assert recovered == trial
