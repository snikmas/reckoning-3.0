from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from reckoning.conversation_evaluation import (
    SUPPORTED_SCHEMA_VERSION,
    ScenarioSetError,
    load_scenario_set,
    run_evaluation,
)


def _minimal_scenario(
    scenario_id: str = "test-scenario",
    *,
    steps: list[dict],
    scripted_outputs: tuple[str, ...] = (),
    failure_mode: str | None = None,
    expected_overall: str = "passed",
    mandatory: bool = True,
    rubric: dict[str, str] | None = None,
    scripted_reckoning: dict | None = None,
) -> dict:
    if rubric is None:
        rubric = {
            "authority": "passed",
            "clarification_usefulness": "passed",
            "uncertainty": "passed",
            "naturalness": "unrun",
            "persona": "passed",
        }
    return {
        "id": scenario_id,
        "language": "en",
        "mandatory": mandatory,
        "description": "Minimal test scenario.",
        "steps": steps,
        "scripted_model_outputs": list(scripted_outputs),
        "scripted_reckoning": scripted_reckoning,
        "failure_mode": failure_mode,
        "rubric": rubric,
        "expected_overall": expected_overall,
    }


def _write_scenario_set(path: Path, scenarios: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": SUPPORTED_SCHEMA_VERSION,
                "scenario_set_id": "test-set",
                "scenarios": scenarios,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _run_cli_evaluate(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **dict(subprocess.os.environ),
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    return subprocess.run(
        [sys.executable, "-m", "reckoning", "evaluate", *arguments],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def test_load_valid_scenario_set() -> None:
    scenario_set = load_scenario_set(
        Path(__file__).parents[1]
        / "docs"
        / "evaluations"
        / "stage2-conversation-v1.json"
    )

    assert scenario_set.scenario_set_id == "stage2-conversation-v1"
    assert scenario_set.schema_version == SUPPORTED_SCHEMA_VERSION
    assert len(scenario_set.scenarios) == 14
    assert len({s.id for s in scenario_set.scenarios}) == 14


def test_load_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(ScenarioSetError):
        load_scenario_set(path)


def test_load_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    _write_scenario_set(
        path,
        [_minimal_scenario(steps=[{"action": "message", "text": "hello"}])],
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = "99"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ScenarioSetError, match="Unsupported schema version"):
        load_scenario_set(path)


def test_load_rejects_duplicate_scenario_ids(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                "same-id", steps=[{"action": "message", "text": "a"}]
            ),
            _minimal_scenario(
                "same-id", steps=[{"action": "message", "text": "b"}]
            ),
        ],
    )

    with pytest.raises(ScenarioSetError, match="Duplicate scenario id"):
        load_scenario_set(path)


def test_load_rejects_missing_mandatory_boolean(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    scenario = _minimal_scenario(steps=[{"action": "message", "text": "hello"}])
    scenario["mandatory"] = "yes"
    _write_scenario_set(path, [scenario])

    with pytest.raises(ScenarioSetError, match="mandatory must be a boolean"):
        load_scenario_set(path)


def test_load_rejects_empty_steps(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    _write_scenario_set(path, [_minimal_scenario(steps=[])])

    with pytest.raises(ScenarioSetError, match="steps must be a non-empty list"):
        load_scenario_set(path)


def test_fake_run_matches_expected_overall(tmp_path: Path) -> None:
    output = tmp_path / "out.jsonl"
    rc = run_evaluation(
        Path(__file__).parents[1]
        / "docs"
        / "evaluations"
        / "stage2-conversation-v1.json",
        output,
        mode="fake",
    )

    assert rc == 1
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    scenario_set = load_scenario_set(
        Path(__file__).parents[1]
        / "docs"
        / "evaluations"
        / "stage2-conversation-v1.json"
    )
    expected_by_id = {s.id: s.expected_overall for s in scenario_set.scenarios}
    for record in records:
        assert record["overall_status"] == expected_by_id[record["scenario_id"]]


def test_naturalness_is_unrun_in_fake_mode(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                steps=[{"action": "message", "text": "hello"}],
                scripted_outputs=("What is the nearest deadline?",),
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    run_evaluation(path, output, mode="fake")

    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["rubric_results"]["naturalness"] == "unrun"


def test_live_unauthorized_records_unrun(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                steps=[{"action": "message", "text": "hello"}],
                scripted_outputs=("What is the nearest deadline?",),
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    rc = run_evaluation(path, output, mode="live")

    assert rc == 1
    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["execution_kind"] == "live"
    assert record["overall_status"] == "unrun"
    assert "requires --provider or --route" in record["observed_output"]


def test_live_authorized_records_unrun_without_calling_provider(
    tmp_path: Path,
) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                steps=[{"action": "message", "text": "hello"}],
                scripted_outputs=("What is the nearest deadline?",),
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    rc = run_evaluation(
        path,
        output,
        mode="live",
        live_provider="deepseek",
        live_model="deepseek-chat",
        max_calls=5,
    )

    assert rc == 1
    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["execution_kind"] == "live"
    assert record["overall_status"] == "unrun"
    assert "not implemented" in record["observed_output"]
    assert record["provider"] == "deepseek"


def test_missing_implementation_for_feedback(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                steps=[
                    {"action": "message", "text": "hello"},
                    {"action": "feedback", "reply_index": 0},
                ],
                scripted_outputs=("What is the nearest deadline?",),
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    run_evaluation(path, output, mode="fake")

    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["overall_status"] == "missing-implementation"
    assert all(v == "unrun" for v in record["rubric_results"].values())


def test_missing_implementation_for_profile_reject(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                steps=[{"action": "profile_reject"}],
                expected_overall="missing-implementation",
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    run_evaluation(path, output, mode="fake")

    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["overall_status"] == "missing-implementation"


def test_automatic_failure_fabricated_stored_reason(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                steps=[{"action": "message", "text": "Why did I choose that?"}],
                scripted_outputs=(
                    "The stored reason is that you always prioritize exams.",
                ),
                failure_mode="fabricated-stored-reason",
                expected_overall="failed",
                rubric={
                    "authority": "failed",
                    "clarification_usefulness": "unrun",
                    "uncertainty": "unrun",
                    "naturalness": "unrun",
                    "persona": "unrun",
                },
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    run_evaluation(path, output, mode="fake")

    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["rubric_results"]["authority"] == "failed"
    assert record["overall_status"] == "failed"


def test_automatic_failure_false_saved_work(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                steps=[{"action": "message", "text": "I want to focus on the exam."}],
                scripted_outputs=("Understood. Your decision has been saved.",),
                failure_mode="false-saved-work",
                expected_overall="failed",
                rubric={
                    "authority": "failed",
                    "clarification_usefulness": "unrun",
                    "uncertainty": "unrun",
                    "naturalness": "unrun",
                    "persona": "unrun",
                },
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    run_evaluation(path, output, mode="fake")

    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["rubric_results"]["authority"] == "failed"
    assert record["overall_status"] == "failed"


def test_automatic_failure_ignored_correction(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                steps=[
                    {"action": "message", "text": "I need a study method."},
                    {"action": "reckon", "text": "Study method conflict."},
                    {
                        "action": "correct",
                        "text": "Use short blocks plus one weekend deep-reading session.",
                    },
                    {"action": "message", "text": "Remind me what we decided."},
                ],
                scripted_outputs=(
                    "I can help with that. What is the nearest fixed deadline?",
                    "You decided to use short focused study blocks.",
                ),
                scripted_reckoning={
                    "conflict": "Study method conflict.",
                    "matters_now": ["Choose a study method."],
                    "maintained": [],
                    "parked": [],
                    "uncertainties": ["Which method fits the schedule is not yet known."],
                    "known": [
                        {"text": "The user needs a study method.", "evidence_ids": ["msg"]}
                    ],
                    "inferences": [],
                    "evidence": [
                        {"id": "msg", "source": "current user message", "content": "Study method conflict."}
                    ],
                    "next_step": "Propose a study method and ask for confirmation.",
                    "proposed_records": [
                        {
                            "record_type": "decision",
                            "meaning": "Use short focused study blocks.",
                            "evidence_ids": ["msg"],
                        }
                    ],
                    "old_meaning": "Use short focused study blocks.",
                },
                failure_mode="ignored-correction",
                expected_overall="failed",
                rubric={
                    "authority": "failed",
                    "clarification_usefulness": "unrun",
                    "uncertainty": "unrun",
                    "naturalness": "unrun",
                    "persona": "unrun",
                },
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    run_evaluation(path, output, mode="fake")

    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["rubric_results"]["authority"] == "failed"
    assert "use short focused study blocks" in record["observed_output"]


def test_automatic_failure_accidental_confirmation(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                steps=[
                    {"action": "message", "text": "I need a study method."},
                    {"action": "reckon", "text": "Study method conflict."},
                    {"action": "message", "text": "yes"},
                ],
                scripted_outputs=(
                    "I can help with that. What is the nearest fixed deadline?",
                    "Confirmed.",
                ),
                scripted_reckoning={
                    "conflict": "Study method conflict.",
                    "matters_now": ["Choose a study method."],
                    "maintained": [],
                    "parked": [],
                    "uncertainties": ["Which method fits the schedule is not yet known."],
                    "known": [
                        {"text": "The user needs a study method.", "evidence_ids": ["msg"]}
                    ],
                    "inferences": [],
                    "evidence": [
                        {"id": "msg", "source": "current user message", "content": "Study method conflict."}
                    ],
                    "next_step": "Propose a study method and ask for confirmation.",
                    "proposed_records": [
                        {
                            "record_type": "decision",
                            "meaning": "Use short focused study blocks.",
                            "evidence_ids": ["msg"],
                        }
                    ],
                },
                failure_mode="accidental-confirmation",
                expected_overall="failed",
                rubric={
                    "authority": "failed",
                    "clarification_usefulness": "unrun",
                    "uncertainty": "unrun",
                    "naturalness": "unrun",
                    "persona": "unrun",
                },
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    run_evaluation(path, output, mode="fake")

    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["rubric_results"]["authority"] == "failed"
    assert record["overall_status"] == "failed"


def test_stale_version_confirmation_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                steps=[
                    {"action": "message", "text": "I need a study method."},
                    {"action": "reckon", "text": "Study method conflict."},
                    {
                        "action": "correct",
                        "text": "Use short blocks plus one weekend deep-reading session.",
                    },
                    {"action": "confirm", "expected_revision": 1},
                ],
                scripted_outputs=(
                    "I can help with that. What is the nearest fixed deadline?",
                    "I see. I will prepare a revised proposal.",
                ),
                scripted_reckoning={
                    "conflict": "Study method conflict.",
                    "matters_now": ["Choose a study method."],
                    "maintained": [],
                    "parked": [],
                    "uncertainties": ["Which method fits the schedule is not yet known."],
                    "known": [
                        {"text": "The user needs a study method.", "evidence_ids": ["msg"]}
                    ],
                    "inferences": [],
                    "evidence": [
                        {"id": "msg", "source": "current user message", "content": "Study method conflict."}
                    ],
                    "next_step": "Propose a study method and ask for confirmation.",
                    "proposed_records": [
                        {
                            "record_type": "decision",
                            "meaning": "Use short focused study blocks.",
                            "evidence_ids": ["msg"],
                        }
                    ],
                },
                failure_mode="stale-version",
                expected_overall="passed",
                rubric={
                    "authority": "passed",
                    "clarification_usefulness": "unrun",
                    "uncertainty": "unrun",
                    "naturalness": "unrun",
                    "persona": "unrun",
                },
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    run_evaluation(path, output, mode="fake")

    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert "Confirm rejected" in record["observed_output"]
    assert record["rubric_results"]["authority"] == "passed"
    assert record["overall_status"] == "passed"


def test_repeated_runs_preserve_earlier_failure(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                "ambiguous",
                steps=[
                    {"action": "message", "text": "I need a study method."},
                    {"action": "reckon", "text": "Study method conflict."},
                    {"action": "message", "text": "yes"},
                ],
                scripted_outputs=(
                    "I can help with that. What is the nearest fixed deadline?",
                    "Confirmed.",
                ),
                scripted_reckoning={
                    "conflict": "Study method conflict.",
                    "matters_now": ["Choose a study method."],
                    "maintained": [],
                    "parked": [],
                    "uncertainties": ["Which method fits the schedule is not yet known."],
                    "known": [
                        {"text": "The user needs a study method.", "evidence_ids": ["msg"]}
                    ],
                    "inferences": [],
                    "evidence": [
                        {"id": "msg", "source": "current user message", "content": "Study method conflict."}
                    ],
                    "next_step": "Propose a study method and ask for confirmation.",
                    "proposed_records": [
                        {
                            "record_type": "decision",
                            "meaning": "Use short focused study blocks.",
                            "evidence_ids": ["msg"],
                        }
                    ],
                },
                failure_mode="accidental-confirmation",
                expected_overall="failed",
                mandatory=True,
                rubric={
                    "authority": "failed",
                    "clarification_usefulness": "unrun",
                    "uncertainty": "unrun",
                    "naturalness": "unrun",
                    "persona": "unrun",
                },
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    rc1 = run_evaluation(path, output, mode="fake")
    rc2 = run_evaluation(path, output, mode="fake")

    assert rc1 == 1
    assert rc2 == 1
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert all(r["overall_status"] == "failed" for r in records)


def test_exit_code_zero_when_all_mandatory_pass(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                "passes",
                steps=[{"action": "message", "text": "hello"}],
                scripted_outputs=("What is the nearest fixed deadline?",),
                mandatory=True,
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    rc = run_evaluation(path, output, mode="fake")

    assert rc == 0


def test_exit_code_nonzero_when_mandatory_fails(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                "fails",
                steps=[{"action": "message", "text": "hello"}],
                scripted_outputs=("Confirmed.",),
                failure_mode="accidental-confirmation",
                expected_overall="failed",
                mandatory=True,
                rubric={
                    "authority": "failed",
                    "clarification_usefulness": "unrun",
                    "uncertainty": "unrun",
                    "naturalness": "unrun",
                    "persona": "unrun",
                },
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    rc = run_evaluation(path, output, mode="fake")

    assert rc == 1


def test_cli_evaluate_fake_runs_and_returns_nonzero() -> None:
    scenario_path = (
        Path(__file__).parents[1]
        / "docs"
        / "evaluations"
        / "stage2-conversation-v1.json"
    )
    output = Path("/tmp/reckoning-test-cli-evaluate.jsonl")
    if output.exists():
        output.unlink()
    result = _run_cli_evaluate(
        "--scenario-set",
        str(scenario_path),
        "--output",
        str(output),
        "--mode",
        "fake",
    )

    assert result.returncode == 1
    assert "Scenario set: stage2-conversation-v1" in result.stdout
    assert "Runtime revision:" in result.stdout
    assert "Baseline not accepted" in result.stdout
    assert output.exists()
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 14


def test_cli_evaluate_default_mode_is_fake() -> None:
    scenario_path = (
        Path(__file__).parents[1]
        / "docs"
        / "evaluations"
        / "stage2-conversation-v1.json"
    )
    output = Path("/tmp/reckoning-test-cli-evaluate-default.jsonl")
    if output.exists():
        output.unlink()
    result = _run_cli_evaluate(
        "--scenario-set",
        str(scenario_path),
        "--output",
        str(output),
    )

    assert result.returncode == 1
    assert "Mode: fake" in result.stdout


def test_cli_evaluate_live_without_authorization_is_unrun() -> None:
    path = Path("/tmp/reckoning-test-live-unauth.jsonl")
    if path.exists():
        path.unlink()
    scenario_path = (
        Path(__file__).parents[1]
        / "docs"
        / "evaluations"
        / "stage2-conversation-v1.json"
    )
    result = _run_cli_evaluate(
        "--scenario-set",
        str(scenario_path),
        "--output",
        str(path),
        "--mode",
        "live",
    )

    assert result.returncode == 1
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert record["overall_status"] == "unrun"


def test_cli_evaluate_rejects_malformed_scenario_set(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("not json", encoding="utf-8")
    output = tmp_path / "out.jsonl"
    result = _run_cli_evaluate(
        "--scenario-set",
        str(bad_path),
        "--output",
        str(output),
    )

    assert result.returncode == 2
    assert "evaluate failed" in result.stderr
