from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from reckoning.conversation_evaluation import (
    SUPPORTED_SCHEMA_VERSION,
    BudgetExhausted,
    LiveBudget,
    LiveConfig,
    ScenarioSetError,
    load_scenario_set,
    run_evaluation,
)


AUTOMATED_RUBRIC = {
    "authority": {
        "evaluator": "authority-v1",
        "human_judgment_required": False,
    },
    "clarification_usefulness": {
        "evaluator": "clarification-v1",
        "human_judgment_required": False,
    },
    "uncertainty": {
        "evaluator": "uncertainty-v1",
        "human_judgment_required": False,
    },
    "naturalness": {
        "evaluator": "human-judgment-v1",
        "human_judgment_required": True,
    },
    "persona": {
        "evaluator": "persona-safety-v1",
        "human_judgment_required": False,
    },
}


def _authority_rubric(evaluator: str) -> dict[str, dict[str, object]]:
    return {
        "authority": {
            "evaluator": evaluator,
            "human_judgment_required": False,
        },
        "naturalness": {
            "evaluator": "human-judgment-v1",
            "human_judgment_required": True,
        },
    }


EXPECTED_STAGE2_FAKE_OUTCOMES = {
    "about-me-correction-exclusion": "missing-implementation",
    "ambiguous-assent-en": "passed",
    "casual-conversation-en": "passed",
    "fabricated-stored-reason-en": "failed",
    "false-saved-work-en": "failed",
    "home-review-surfaces": "missing-implementation",
    "ignored-correction-en": "failed",
    "mixed-language-und": "passed",
    "multilingual-correction-ru": "passed",
    "multilingual-correction-zh": "passed",
    "outcome-check-in-en": "missing-implementation",
    "rejected-facts-en": "missing-implementation",
    "reply-feedback-en": "missing-implementation",
    "resume-decision-en": "missing-implementation",
    "richer-decision-lifecycle": "missing-implementation",
    "simon-persona-en": "passed",
    "stale-version-en": "passed",
    "telegram-continuation": "missing-implementation",
    "uncertainty-en": "passed",
    "useful-clarification-en": "passed",
}


def _study_method_reckoning(
    meaning: str = "Use short focused study blocks.",
) -> dict[str, object]:
    return {
        "conflict": "Study method conflict.",
        "matters_now": ["Choose a study method."],
        "maintained": [],
        "parked": [],
        "uncertainties": ["Which method fits the schedule is not yet known."],
        "known": [{"text": "The user needs a study method.", "evidence_ids": ["msg"]}],
        "inferences": [],
        "evidence": [
            {
                "id": "msg",
                "source": "current user message",
                "content": "Study method conflict.",
            }
        ],
        "next_step": "Propose a study method and ask for confirmation.",
        "proposed_records": [
            {
                "record_type": "decision",
                "meaning": meaning,
                "evidence_ids": ["msg"],
            }
        ],
    }


def _minimal_scenario(
    scenario_id: str = "test-scenario",
    *,
    steps: list[dict],
    scripted_outputs: tuple[str, ...] = (),
    mandatory: bool = True,
    rubric: dict[str, dict[str, object]] | None = None,
    scripted_reckoning: dict | None = None,
    tags: list[str] | None = None,
) -> dict:
    if rubric is None:
        rubric = AUTOMATED_RUBRIC
    return {
        "id": scenario_id,
        "language": "en",
        "mandatory": mandatory,
        "description": "Minimal test scenario.",
        "steps": steps,
        "scripted_model_outputs": list(scripted_outputs),
        "scripted_reckoning": scripted_reckoning,
        "rubric": rubric,
        "tags": list(tags or []),
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
        Path(__file__).parents[1] / "scenarios" / "stage2-conversation-v1.json"
    )

    assert scenario_set.scenario_set_id == "stage2-conversation-v1"
    assert scenario_set.schema_version == SUPPORTED_SCHEMA_VERSION
    assert len(scenario_set.scenarios) == 20
    assert len({s.id for s in scenario_set.scenarios}) == 20


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
            _minimal_scenario("same-id", steps=[{"action": "message", "text": "a"}]),
            _minimal_scenario("same-id", steps=[{"action": "message", "text": "b"}]),
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


def test_load_rejects_unknown_evaluator_rule(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                steps=[{"action": "message", "text": "hello"}],
                rubric={
                    "authority": {
                        "evaluator": "authority-v99",
                        "human_judgment_required": False,
                    }
                },
            )
        ],
    )

    with pytest.raises(ScenarioSetError, match="unknown rule"):
        load_scenario_set(path)


def test_load_rejects_malformed_dimension_definition(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    scenario = _minimal_scenario(steps=[{"action": "message", "text": "hello"}])
    scenario["rubric"] = {"authority": "passed"}
    _write_scenario_set(path, [scenario])

    with pytest.raises(ScenarioSetError, match="rubric.authority must be an object"):
        load_scenario_set(path)


def test_profile_selecting_no_mandatory_scenarios_is_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                steps=[{"action": "message", "text": "hello"}],
                mandatory=False,
                tags=["early-web"],
            )
        ],
    )
    output = tmp_path / "out.jsonl"

    with pytest.raises(ValueError, match="no mandatory scenarios"):
        run_evaluation(path, output, mode="fake", profile="early-web")


def test_unknown_profile_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [_minimal_scenario(steps=[{"action": "message", "text": "hello"}])],
    )
    output = tmp_path / "out.jsonl"

    with pytest.raises(ValueError, match="Unknown profile"):
        run_evaluation(path, output, mode="fake", profile="unknown")

    assert not output.exists()


def test_fake_run_matches_test_only_fixture_outcomes(tmp_path: Path) -> None:
    output = tmp_path / "out.jsonl"
    rc = run_evaluation(
        Path(__file__).parents[1] / "scenarios" / "stage2-conversation-v1.json",
        output,
        mode="fake",
    )

    assert rc == 1
    records = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
    for record in records:
        assert (
            record["overall_status"]
            == EXPECTED_STAGE2_FAKE_OUTCOMES[record["scenario_id"]]
        )


def test_fixture_expectation_cannot_influence_runtime_score(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                steps=[{"action": "message", "text": "Help me choose."}],
                scripted_outputs=("Maybe choose the first option.",),
            )
        ],
    )

    scores = []
    fixture_matches = []
    fixture_expectations = ("passed", "failed")
    for index, expected_fixture_outcome in enumerate(fixture_expectations):
        output = tmp_path / f"out-{index}.jsonl"
        run_evaluation(path, output, mode="fake")
        record = json.loads(output.read_text(encoding="utf-8"))
        scores.append((record["rubric_results"], record["overall_status"]))
        fixture_matches.append(record["overall_status"] == expected_fixture_outcome)

    assert fixture_expectations[0] != fixture_expectations[1]
    assert scores[0] == scores[1]
    assert fixture_matches == [False, True]


def test_product_and_detector_counts_are_reported_separately(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "out.jsonl"
    run_evaluation(
        Path(__file__).parents[1] / "scenarios" / "stage2-conversation-v1.json",
        output,
        mode="fake",
    )

    stdout = capsys.readouterr().out
    assert "Product counts: {'passed': 9, 'missing-implementation': 8}" in stdout
    assert "Detector counts: {'failed': 3}" in stdout

    records = [json.loads(line) for line in output.read_text().splitlines()]
    product = [record for record in records if "product" in record["scenario_tags"]]
    detector = [record for record in records if "detector" in record["scenario_tags"]]
    assert len(product) == 17
    assert len(detector) == 3
    assert {record["overall_status"] for record in detector} == {"failed"}


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
    assert "explicit mode" in record["observed_output"]


def test_live_authorized_records_unrun_without_credentials(
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
    missing_credentials = tmp_path / "no-credentials.json"
    rc = run_evaluation(
        path,
        output,
        mode="live",
        live_provider="deepseek",
        live_model="deepseek-chat",
        max_calls=5,
        live_processing_permitted=True,
        acknowledge_unknown_cost=True,
        credentials_path=missing_credentials,
    )

    assert rc == 1
    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["execution_kind"] == "live"
    assert record["overall_status"] == "unrun"
    assert "could not start" in record["observed_output"]
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
                rubric=_authority_rubric("authority-no-fabricated-reason-v1"),
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
                rubric=_authority_rubric("authority-no-false-save-v1"),
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    run_evaluation(path, output, mode="fake")

    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["rubric_results"]["authority"] == "failed"
    assert record["overall_status"] == "failed"


def test_ambiguous_confirmation_rule_fails_confirmed_state(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                steps=[
                    {"action": "reckon", "text": "Study method conflict."},
                    {"action": "confirm"},
                ],
                scripted_reckoning=_study_method_reckoning(),
                rubric=_authority_rubric("authority-no-ambiguous-confirmation-v1"),
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    run_evaluation(path, output, mode="fake")

    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["state_evidence"]["confirmed_reckoning_count"] == 1
    assert record["rubric_results"]["authority"] == "failed"
    assert record["overall_status"] == "failed"


def test_repeated_corrections_use_rendered_forms_and_state_history(
    tmp_path: Path,
) -> None:
    path = tmp_path / "set.json"
    final_meaning = "Use two short blocks and one weekend review."
    _write_scenario_set(
        path,
        [
            _minimal_scenario(
                steps=[
                    {"action": "reckon", "text": "Study method conflict."},
                    {
                        "action": "correct",
                        "text": "Use one short block and one weekend review.",
                    },
                    {"action": "correct", "text": final_meaning},
                ],
                scripted_reckoning=_study_method_reckoning(),
                rubric=_authority_rubric("authority-v1"),
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    run_evaluation(path, output, mode="fake")

    record = json.loads(output.read_text(encoding="utf-8"))
    evidence = record["state_evidence"]
    corrections = [
        transition
        for transition in evidence["state_transitions"]
        if transition["action"] == "correct"
    ]
    assert evidence["last_reckoning_version"] == 3
    assert evidence["last_reckoning_meanings"] == [final_meaning]
    assert len(evidence["superseded_reckoning_meanings"]) == 2
    assert [item["before_revision"] for item in corrections] == [1, 2]
    assert [item["after_revision"] for item in corrections] == [2, 3]
    assert all(item["rendered_binding_present"] for item in corrections)
    assert all(item["durable_receipt"]["status"] == "completed" for item in corrections)


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
                    "uncertainties": [
                        "Which method fits the schedule is not yet known."
                    ],
                    "known": [
                        {
                            "text": "The user needs a study method.",
                            "evidence_ids": ["msg"],
                        }
                    ],
                    "inferences": [],
                    "evidence": [
                        {
                            "id": "msg",
                            "source": "current user message",
                            "content": "Study method conflict.",
                        }
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
                rubric=_authority_rubric("authority-correction-preserved-v1"),
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    run_evaluation(path, output, mode="fake")

    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["rubric_results"]["authority"] == "failed"
    assert "use short focused study blocks" in record["observed_output"]


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
                    "uncertainties": [
                        "Which method fits the schedule is not yet known."
                    ],
                    "known": [
                        {
                            "text": "The user needs a study method.",
                            "evidence_ids": ["msg"],
                        }
                    ],
                    "inferences": [],
                    "evidence": [
                        {
                            "id": "msg",
                            "source": "current user message",
                            "content": "Study method conflict.",
                        }
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
                rubric=_authority_rubric("authority-stale-confirmation-rejected-v1"),
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
                scripted_outputs=("Understood. Your decision has been saved.",),
                mandatory=True,
                rubric=_authority_rubric("authority-no-false-save-v1"),
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    rc1 = run_evaluation(path, output, mode="fake")
    rc2 = run_evaluation(path, output, mode="fake")

    assert rc1 == 1
    assert rc2 == 1
    records = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
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
                scripted_outputs=("Understood. Your decision has been saved.",),
                mandatory=True,
                rubric=_authority_rubric("authority-no-false-save-v1"),
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    rc = run_evaluation(path, output, mode="fake")

    assert rc == 1


def test_cli_evaluate_fake_runs_and_returns_nonzero() -> None:
    scenario_path = (
        Path(__file__).parents[1] / "scenarios" / "stage2-conversation-v1.json"
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
    records = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 20


def test_cli_evaluate_default_mode_is_fake() -> None:
    scenario_path = (
        Path(__file__).parents[1] / "scenarios" / "stage2-conversation-v1.json"
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
        Path(__file__).parents[1] / "scenarios" / "stage2-conversation-v1.json"
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


def _write_credentials(
    path: Path, provider: str, api_key: str, model: str | None = None
) -> None:
    entry: dict[str, object] = {"secret": api_key, "verified": True}
    if model is not None:
        entry["model"] = model
    path.write_text(
        json.dumps({"providers": {provider: entry}}),
        encoding="utf-8",
    )


def _openai_response(content: str, usage: dict[str, object] | None = None) -> bytes:
    payload: dict[str, object] = {
        "choices": [{"message": {"content": content}}],
        "model": "fake-model",
    }
    if usage is not None:
        payload["usage"] = usage
    return json.dumps(payload).encode("utf-8")


def _reckoning_json() -> str:
    return json.dumps(
        {
            "conflict": "Study conflict",
            "questions": [],
            "matters_now": ["Choose a study method"],
            "maintained": [],
            "parked": [],
            "uncertainties": ["Schedule is unknown"],
            "known": ["User needs a study method"],
            "inferences": [],
            "next_step": "Confirm or correct the proposal",
            "proposed_records": [
                {
                    "record_type": "decision",
                    "meaning": "Use short focused study blocks",
                }
            ],
        }
    )


class _FakeLiveTransport:
    """A deterministic transport that mimics a real provider without network calls."""

    def __init__(self, responses: list[bytes | Exception] | None = None) -> None:
        self.calls = 0
        self.requests: list[Request] = []
        self._responses = responses or []
        self._index = 0

    def __call__(self, request: Request, timeout: float) -> bytes:
        self.calls += 1
        self.requests.append(request)
        if self._index < len(self._responses):
            response = self._responses[self._index]
            self._index += 1
            if isinstance(response, Exception):
                raise response
            return response
        body = json.loads(request.data.decode("utf-8"))
        messages = body.get("messages", [])
        is_reckon = any(
            "structured_reckoning" in m.get("content", "") for m in messages
        )
        if is_reckon:
            content = _reckoning_json()
        else:
            content = "I can help with that. What is the nearest fixed deadline?"
        return _openai_response(
            content,
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        )


def test_live_mode_runs_through_adapter_with_injected_transport(
    tmp_path: Path,
) -> None:
    creds = tmp_path / "creds.json"
    _write_credentials(creds, "ollama", "unused", model="qwen3")
    scenario_path = tmp_path / "set.json"
    _write_scenario_set(
        scenario_path,
        [
            _minimal_scenario(
                steps=[{"action": "message", "text": "hello"}],
            )
        ],
    )
    output = tmp_path / "out.jsonl"
    transport = _FakeLiveTransport()

    rc = run_evaluation(
        scenario_path,
        output,
        mode="live",
        live_provider="ollama",
        live_model="qwen3",
        max_calls=5,
        live_processing_permitted=True,
        acknowledge_unknown_cost=True,
        credentials_path=creds,
        transport=transport,
    )

    assert rc == 0
    assert transport.calls >= 1
    assert all("/chat/completions" in str(r.full_url) for r in transport.requests)
    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["execution_kind"] == "live"
    assert record["overall_status"] == "passed"
    assert record["provider"] == "ollama"
    assert record["cost_status"] == "estimated"
    assert record["input_tokens"] == 10
    assert record["output_tokens"] == 5


def test_live_call_budget_stops_before_exceeding_limit(tmp_path: Path) -> None:
    creds = tmp_path / "creds.json"
    _write_credentials(creds, "ollama", "unused", model="qwen3")
    scenario_path = tmp_path / "set.json"
    _write_scenario_set(
        scenario_path,
        [
            _minimal_scenario(
                "first",
                steps=[{"action": "message", "text": "hello"}],
            ),
            _minimal_scenario(
                "second",
                steps=[{"action": "message", "text": "hello again"}],
            ),
        ],
    )
    output = tmp_path / "out.jsonl"
    transport = _FakeLiveTransport()

    rc = run_evaluation(
        scenario_path,
        output,
        mode="live",
        live_provider="ollama",
        live_model="qwen3",
        max_calls=1,
        live_processing_permitted=True,
        acknowledge_unknown_cost=True,
        credentials_path=creds,
        transport=transport,
    )

    assert rc == 1
    assert transport.calls == 1
    records = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 2
    assert records[0]["overall_status"] == "passed"
    assert records[1]["overall_status"] == "unrun"
    assert "budget" in records[1]["observed_output"].lower()


def test_live_proposal_and_message_calls_share_one_budget(tmp_path: Path) -> None:
    creds = tmp_path / "creds.json"
    _write_credentials(creds, "ollama", "unused", model="qwen3")
    scenario_path = tmp_path / "set.json"
    _write_scenario_set(
        scenario_path,
        [
            _minimal_scenario(
                "proposal",
                steps=[{"action": "reckon", "text": "hello"}],
            ),
            _minimal_scenario(
                "chat",
                steps=[{"action": "message", "text": "hello again"}],
            ),
        ],
    )
    output = tmp_path / "out.jsonl"
    transport = _FakeLiveTransport()

    rc = run_evaluation(
        scenario_path,
        output,
        mode="live",
        live_provider="ollama",
        live_model="qwen3",
        max_calls=1,
        live_processing_permitted=True,
        acknowledge_unknown_cost=True,
        credentials_path=creds,
        transport=transport,
    )

    assert rc == 1
    assert transport.calls == 1
    records = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert records[1]["overall_status"] == "unrun"


def test_live_cost_budget_reserves_before_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reckoning import conversation_evaluation as eval_module

    monkeypatch.setitem(
        eval_module.PRICE_BASIS,
        "ollama",
        eval_module.PriceBasis("USD", "test", 1.0, 2.0, True),
    )
    creds = tmp_path / "creds.json"
    _write_credentials(creds, "ollama", "unused", model="qwen3")
    scenario_path = tmp_path / "set.json"
    _write_scenario_set(
        scenario_path,
        [
            _minimal_scenario(
                "first",
                steps=[{"action": "reckon", "text": "hello"}],
                scripted_reckoning={
                    "conflict": "Study method conflict.",
                    "matters_now": ["Choose a study method."],
                    "maintained": [],
                    "parked": [],
                    "uncertainties": [
                        "Which method fits the schedule is not yet known."
                    ],
                    "known": [
                        {
                            "text": "The user needs a study method.",
                            "evidence_ids": ["msg"],
                        }
                    ],
                    "inferences": [],
                    "evidence": [
                        {
                            "id": "msg",
                            "source": "current user message",
                            "content": "Study method conflict.",
                        }
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
            ),
        ],
    )
    output = tmp_path / "out.jsonl"
    transport = _FakeLiveTransport()

    rc = run_evaluation(
        scenario_path,
        output,
        mode="live",
        live_provider="ollama",
        live_model="qwen3",
        max_calls=5,
        max_cost=0.00001,
        live_processing_permitted=True,
        acknowledge_unknown_cost=True,
        credentials_path=creds,
        transport=transport,
    )

    assert rc == 1
    assert transport.calls == 0
    records = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["overall_status"] in ("unrun", "missing-implementation")
    assert "budget" in records[0]["observed_output"].lower()


def test_live_budget_reserves_outstanding_cost_per_attempt() -> None:
    # DeepSeek's versioned basis reserves about $0.004096 per attempt.
    budget = LiveBudget(provider="deepseek", max_calls=10, max_cost=0.006)

    budget.check_call()
    assert budget.calls_used == 1

    with pytest.raises(BudgetExhausted):
        budget.check_call()
    assert budget.calls_used == 1


def test_live_config_requires_model_permission_and_cost_acknowledgement() -> None:
    base = {
        "enabled": True,
        "provider": "ollama",
        "model": "qwen3",
        "route": None,
        "max_calls": 5,
        "max_cost": None,
    }
    assert not LiveConfig(
        **base, processing_permitted=False, unknown_cost_acknowledged=True
    ).authorized
    assert not LiveConfig(
        **base, processing_permitted=True, unknown_cost_acknowledged=False
    ).authorized
    assert not LiveConfig(
        **{**base, "model": None},
        processing_permitted=True,
        unknown_cost_acknowledged=True,
    ).authorized
    assert not LiveConfig(
        **{**base, "max_calls": None},
        processing_permitted=True,
        unknown_cost_acknowledged=True,
    ).authorized
    assert not LiveConfig(
        **{**base, "provider": None, "route": None},
        processing_permitted=True,
        unknown_cost_acknowledged=True,
    ).authorized
    assert LiveConfig(
        **base, processing_permitted=True, unknown_cost_acknowledged=True
    ).authorized


def test_live_missing_permission_makes_zero_transport_calls(tmp_path: Path) -> None:
    creds = tmp_path / "creds.json"
    _write_credentials(creds, "ollama", "unused", model="qwen3")
    scenario_path = tmp_path / "set.json"
    _write_scenario_set(
        scenario_path,
        [_minimal_scenario(steps=[{"action": "message", "text": "hello"}])],
    )
    output = tmp_path / "out.jsonl"
    transport = _FakeLiveTransport()

    rc = run_evaluation(
        scenario_path,
        output,
        mode="live",
        live_provider="ollama",
        live_model="qwen3",
        max_calls=5,
        acknowledge_unknown_cost=True,
        credentials_path=creds,
        transport=transport,
    )

    assert rc == 1
    assert transport.calls == 0
    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["overall_status"] == "unrun"
    assert "permission" in record["observed_output"].lower()


def test_live_cost_only_authorization_is_rejected(tmp_path: Path) -> None:
    creds = tmp_path / "creds.json"
    _write_credentials(creds, "ollama", "unused", model="qwen3")
    scenario_path = tmp_path / "set.json"
    _write_scenario_set(
        scenario_path,
        [_minimal_scenario(steps=[{"action": "message", "text": "hello"}])],
    )
    output = tmp_path / "out.jsonl"

    rc = run_evaluation(
        scenario_path,
        output,
        mode="live",
        live_provider="ollama",
        live_model="qwen3",
        max_cost=1,
        credentials_path=creds,
    )

    assert rc == 1
    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["execution_kind"] == "live"
    assert record["overall_status"] == "unrun"
    assert "max-calls" in record["observed_output"].lower()


def test_live_malformed_provider_response_records_failure(tmp_path: Path) -> None:
    creds = tmp_path / "creds.json"
    _write_credentials(creds, "ollama", "unused", model="qwen3")
    scenario_path = tmp_path / "set.json"
    _write_scenario_set(
        scenario_path,
        [_minimal_scenario(steps=[{"action": "message", "text": "hello"}])],
    )
    output = tmp_path / "out.jsonl"
    transport = _FakeLiveTransport(responses=[b"not valid json"])

    rc = run_evaluation(
        scenario_path,
        output,
        mode="live",
        live_provider="ollama",
        live_model="qwen3",
        max_calls=5,
        live_processing_permitted=True,
        acknowledge_unknown_cost=True,
        credentials_path=creds,
        transport=transport,
    )

    assert rc == 1
    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["execution_kind"] == "live"
    assert record["overall_status"] != "passed"


def test_live_missing_usage_records_unavailable_cost(tmp_path: Path) -> None:
    creds = tmp_path / "creds.json"
    _write_credentials(creds, "ollama", "unused", model="qwen3")
    scenario_path = tmp_path / "set.json"
    _write_scenario_set(
        scenario_path,
        [_minimal_scenario(steps=[{"action": "message", "text": "hello"}])],
    )
    output = tmp_path / "out.jsonl"
    transport = _FakeLiveTransport(
        responses=[_openai_response("I can help with that.")]
    )

    rc = run_evaluation(
        scenario_path,
        output,
        mode="live",
        live_provider="ollama",
        live_model="qwen3",
        max_calls=5,
        live_processing_permitted=True,
        acknowledge_unknown_cost=True,
        credentials_path=creds,
        transport=transport,
    )

    assert rc == 1
    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["cost_status"] == "unavailable"
    assert record["attempt_receipts"][0]["cost_status"] == "unavailable"
    assert record["unknown_cost_attempts"] == 1


def test_live_records_provider_reported_cost_per_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reckoning import conversation_evaluation as eval_module

    monkeypatch.setitem(
        eval_module.PRICE_BASIS,
        "ollama",
        eval_module.PriceBasis("USD", "test-prices", 1.0, 1.0, True),
    )
    creds = tmp_path / "creds.json"
    _write_credentials(creds, "ollama", "unused", model="qwen3")
    scenario_path = tmp_path / "set.json"
    _write_scenario_set(
        scenario_path,
        [_minimal_scenario(steps=[{"action": "message", "text": "hello"}])],
    )
    output = tmp_path / "out.jsonl"
    transport = _FakeLiveTransport(
        responses=[
            _openai_response(
                "I can help with that. What is the nearest fixed deadline?",
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "cost": 0.004,
                    "cost_currency": "USD",
                },
            )
        ]
    )

    rc = run_evaluation(
        scenario_path,
        output,
        mode="live",
        live_provider="ollama",
        live_model="qwen3",
        max_calls=5,
        max_cost=0.02,
        live_processing_permitted=True,
        credentials_path=creds,
        transport=transport,
    )

    assert rc == 0
    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["cost_status"] == "measured"
    assert record["cost_amount"] == 0.004
    assert record["provider_reported_cost_amount"] == 0.004
    receipt = record["attempt_receipts"][0]
    assert receipt["call_number"] == 1
    assert receipt["completion_status"] == "response-received"
    assert receipt["reserved_cost_amount"] == pytest.approx(0.00512)
    assert receipt["actual_cost_amount"] == 0.004
    assert receipt["actual_cost_currency"] == "USD"
    assert receipt["cost_status"] == "measured"
    assert receipt["quote_overrun"] is False
    assert record["spending_status"] == "within-authorization"


def test_provider_reported_cost_over_quote_stops_the_next_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reckoning import conversation_evaluation as eval_module

    monkeypatch.setitem(
        eval_module.PRICE_BASIS,
        "ollama",
        eval_module.PriceBasis("USD", "test-prices", 1.0, 1.0, True),
    )
    creds = tmp_path / "creds.json"
    _write_credentials(creds, "ollama", "unused", model="qwen3")
    scenario_path = tmp_path / "set.json"
    _write_scenario_set(
        scenario_path,
        [
            _minimal_scenario("first", steps=[{"action": "message", "text": "one"}]),
            _minimal_scenario("second", steps=[{"action": "message", "text": "two"}]),
        ],
    )
    output = tmp_path / "out.jsonl"
    response = _openai_response(
        "I can help with that.",
        usage={
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cost": 0.02,
            "cost_currency": "USD",
        },
    )
    transport = _FakeLiveTransport(responses=[response, response])

    rc = run_evaluation(
        scenario_path,
        output,
        mode="live",
        live_provider="ollama",
        live_model="qwen3",
        max_calls=5,
        max_cost=0.10,
        live_processing_permitted=True,
        credentials_path=creds,
        transport=transport,
    )

    assert rc == 1
    assert transport.calls == 1
    first, second = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert first["overall_status"] == "failed"
    assert first["spending_status"] == "quote-overrun"
    assert first["attempt_receipts"][0]["quote_overrun"] is True
    assert second["overall_status"] == "unrun"
    assert "budget" in second["observed_output"].lower()


def test_live_retry_receipts_preserve_failed_attempt_and_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reckoning import conversation_evaluation as eval_module

    monkeypatch.setitem(
        eval_module.PRICE_BASIS,
        "ollama",
        eval_module.PriceBasis("USD", "test-prices", 1.0, 1.0, True),
    )
    creds = tmp_path / "creds.json"
    _write_credentials(creds, "ollama", "unused", model="qwen3")
    scenario_path = tmp_path / "set.json"
    _write_scenario_set(
        scenario_path,
        [_minimal_scenario(steps=[{"action": "message", "text": "hello"}])],
    )
    output = tmp_path / "out.jsonl"
    transport = _FakeLiveTransport(
        responses=[
            HTTPError("https://example.invalid", 503, "busy", {}, None),
            _openai_response(
                "I can help with that. What is the nearest fixed deadline?",
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            ),
        ]
    )

    rc = run_evaluation(
        scenario_path,
        output,
        mode="live",
        live_provider="ollama",
        live_model="qwen3",
        max_calls=5,
        max_cost=0.02,
        live_processing_permitted=True,
        credentials_path=creds,
        transport=transport,
    )

    assert rc == 0
    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    receipts = record["attempt_receipts"]
    assert [item["call_number"] for item in receipts] == [1, 2]
    assert [item["completion_status"] for item in receipts] == [
        "transport-failed",
        "response-received",
    ]
    assert receipts[0]["failure_kind"] == "HTTPError"
    assert receipts[0]["reserved_cost_amount"] == pytest.approx(0.00512)
    assert receipts[1]["estimated_cost_amount"] == pytest.approx(0.000015)


def test_live_mid_suite_failure_keeps_completed_and_failed_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reckoning import conversation_evaluation as eval_module

    monkeypatch.setitem(
        eval_module.PRICE_BASIS,
        "ollama",
        eval_module.PriceBasis("USD", "test-prices", 1.0, 1.0, True),
    )
    creds = tmp_path / "creds.json"
    _write_credentials(creds, "ollama", "unused", model="qwen3")
    scenario_path = tmp_path / "set.json"
    _write_scenario_set(
        scenario_path,
        [
            _minimal_scenario("first", steps=[{"action": "message", "text": "one"}]),
            _minimal_scenario("second", steps=[{"action": "message", "text": "two"}]),
        ],
    )
    output = tmp_path / "out.jsonl"
    transport = _FakeLiveTransport(
        responses=[
            _openai_response(
                "I can help with that. What is the nearest fixed deadline?",
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            ),
            HTTPError("https://example.invalid", 503, "busy", {}, None),
            HTTPError("https://example.invalid", 503, "busy", {}, None),
            HTTPError("https://example.invalid", 503, "busy", {}, None),
        ]
    )

    rc = run_evaluation(
        scenario_path,
        output,
        mode="live",
        live_provider="ollama",
        live_model="qwen3",
        max_calls=10,
        max_cost=0.10,
        live_processing_permitted=True,
        credentials_path=creds,
        transport=transport,
    )

    assert rc == 1
    first, second = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert first["attempt_receipts"][0]["completion_status"] == "response-received"
    assert [item["completion_status"] for item in second["attempt_receipts"]] == [
        "transport-failed",
        "transport-failed",
        "transport-failed",
    ]
    assert [item["call_number"] for item in second["attempt_receipts"]] == [2, 3, 4]
    assert all(
        item["reserved_cost_amount"] == pytest.approx(0.00512)
        for item in second["attempt_receipts"]
    )


def test_negative_provider_reported_cost_is_rejected_and_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reckoning import conversation_evaluation as eval_module

    monkeypatch.setitem(
        eval_module.PRICE_BASIS,
        "ollama",
        eval_module.PriceBasis("USD", "test-prices", 1.0, 1.0, True),
    )
    creds = tmp_path / "creds.json"
    _write_credentials(creds, "ollama", "unused", model="qwen3")
    scenario_path = tmp_path / "set.json"
    _write_scenario_set(
        scenario_path,
        [_minimal_scenario(steps=[{"action": "message", "text": "hello"}])],
    )
    output = tmp_path / "out.jsonl"
    transport = _FakeLiveTransport(
        responses=[
            _openai_response(
                "I can help with that. What is the nearest fixed deadline?",
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "cost": -0.01,
                    "cost_currency": "USD",
                },
            )
        ]
    )

    rc = run_evaluation(
        scenario_path,
        output,
        mode="live",
        live_provider="ollama",
        live_model="qwen3",
        max_calls=5,
        max_cost=0.02,
        live_processing_permitted=True,
        credentials_path=creds,
        transport=transport,
    )

    assert rc == 1
    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    receipt = record["attempt_receipts"][0]
    assert receipt["completion_status"] == "invalid-provider-cost"
    assert receipt["failure_kind"] == "invalid-amount"
    assert receipt["actual_cost_amount"] is None
    assert record["spending_status"] == "invalid-provider-cost"


def test_interrupted_live_run_keeps_completed_attempt_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reckoning import conversation_evaluation as eval_module

    monkeypatch.setitem(
        eval_module.PRICE_BASIS,
        "ollama",
        eval_module.PriceBasis("USD", "test-prices", 1.0, 1.0, True),
    )
    creds = tmp_path / "creds.json"
    _write_credentials(creds, "ollama", "unused", model="qwen3")
    scenario_path = tmp_path / "set.json"
    _write_scenario_set(
        scenario_path,
        [
            _minimal_scenario("first", steps=[{"action": "message", "text": "one"}]),
            _minimal_scenario("second", steps=[{"action": "message", "text": "two"}]),
        ],
    )
    output = tmp_path / "out.jsonl"
    transport = _FakeLiveTransport()
    original = eval_module.evaluate_scenario
    invocation_count = 0

    def interrupt_before_second(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal invocation_count
        invocation_count += 1
        if invocation_count == 2:
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    monkeypatch.setattr(eval_module, "evaluate_scenario", interrupt_before_second)

    with pytest.raises(KeyboardInterrupt):
        run_evaluation(
            scenario_path,
            output,
            mode="live",
            live_provider="ollama",
            live_model="qwen3",
            max_calls=5,
            max_cost=0.02,
            live_processing_permitted=True,
            credentials_path=creds,
            transport=transport,
        )

    records = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["scenario_id"] == "first"
    assert records[0]["attempt_receipts"][0]["completion_status"] == (
        "response-received"
    )
