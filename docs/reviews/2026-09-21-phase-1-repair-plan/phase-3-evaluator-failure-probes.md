# Phase 3: Add failure probes through the real web journey

[Back to the overview](overview.md)

## Goal

Prove that the evaluator detects product failures through rendered web actions
and keeps detector success separate from product acceptance.

## TODO

- [ ] Add a metamorphic test that changes the declared expected result while
  holding the observation and state evidence constant.
- [ ] Prove that a question containing `confirm` does not become consent.
- [ ] Prove that ambiguous assent does not confirm a proposal.
- [ ] Prove that a stale displayed revision fails through the submitted form.
- [ ] Prove that repeated correction and replay preserve one authoritative
  operation result across reconstruction.
- [ ] Prove that fabricated stored reasons, ignored corrections, accidental
  confirmation, and false saved-work claims stay failed product cases even when
  the detector test itself passes.
- [ ] Prove that a missing UI operation is `missing-implementation`, an
  unauthorized run is `unrun`, and neither can satisfy a mandatory case.
- [ ] Inspect the produced JSONL to ensure detector counts and product counts
  cannot be combined.

## Files

- `src/reckoning/evaluation_web_driver.py` only if the real form path needs a
  narrow correction.
- `tests/test_conversation_evaluation.py`.
- `tests/test_web_decision_journey.py`.

## Data structures

Each web step carries the rendered session token, CSRF token, displayed
revision, and operation ID. State evidence records the before and after
revision plus the durable operation receipt.

## Verification

- Static: focused tests, mypy, Ruff, and `git diff --check`.
- Runtime: drive the local fake-provider web journey through rendered forms.
  Use the `browse` skill for a browser smoke after the automated tests.
- Stop condition: a helper call that mutates application state directly cannot
  stand in for a missing browser action.
