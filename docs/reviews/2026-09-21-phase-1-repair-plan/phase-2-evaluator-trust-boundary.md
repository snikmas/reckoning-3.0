# Phase 2: Remove expected-result influence from scoring

[Back to the overview](overview.md)

## Goal

Make evaluator results a function of observed behavior and authoritative state,
not the answer declared by the fixture.

## TODO

- [ ] Trace every field from scenario JSON through `Scenario`,
  `_evaluate_rubric`, the dimension evaluators, `overall_status`, and JSONL.
- [ ] Separate the scoring rule from the expected fixture result. In
  particular, remove `expected` as an input to clarification, uncertainty, and
  persona scoring.
- [ ] Keep human-only language dimensions, including naturalness, `unrun` until
  a dated human judgment is supplied.
- [ ] Keep expected fixture outcomes only as test or benchmark assertions. Do
  not expose them to the function that computes a product result.
- [ ] Version the scenario schema if the persisted shape changes. Preserve the
  scenario-set and per-scenario digests in output records.
- [ ] Reject unknown evaluator rules and malformed dimension definitions during
  scenario loading.

## Files

- `src/reckoning/conversation_evaluation.py`.
- `scenarios/stage2-conversation-v1.json` if the schema changes.
- `tests/test_conversation_evaluation.py`.

## Data structures

Use a rubric rule that names the evaluator and whether human judgment is
required. Keep any `expected_fixture_outcome` in a separate test-only shape.
The runtime `rubric_results` map contains observed statuses only.

## Verification

- Static: mypy, Ruff, scenario validation tests, and `git diff --check`.
- Runtime: run the focused evaluator tests and a fake `early-web` evaluation.
- Required invariant: changing only a fixture expectation does not change the
  generated `rubric_results` or `overall_status`.
