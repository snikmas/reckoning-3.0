# Phase 1: Reopen #136 and freeze the repair contract

[Back to the overview](overview.md)

## Goal

Turn the ambiguous closed state of #136 into an explicit repair contract before
changing code.

## TODO

- [ ] Recheck `main`, `origin/main`, the working tree, and #136 immediately
  before implementation.
- [ ] Reopen #136 and add the `partial` label.
- [ ] Add a short issue comment that records the current facts: `0b03c94` is on
  `main`, `c1991f3` supplied the scenario set after closure, no closure evidence
  exists, the documented fake command is absent, and expected rubric values can
  influence computed scores.
- [ ] State that #136 remains the existing repair issue. Do not create a new
  evaluator-trust issue.
- [ ] Freeze the repair acceptance checks from the overview. If the code review
  finds a separate security or paid-provider defect, stop and scope that defect
  independently instead of expanding #136 silently.

## Files and records

- GitHub issue #136.
- `src/reckoning/conversation_evaluation.py`.
- `src/reckoning/evaluation_web_driver.py`.
- `scenarios/stage2-conversation-v1.json`.
- `tests/test_conversation_evaluation.py`.

## Data structures

No production data changes. Record the review as a criterion-by-criterion issue
comment with `fixed`, `partial`, `blocked`, or `unrun` for each criterion.

## Verification

- Static: compare the issue body, current files, and commit ancestry.
- Runtime: none. This phase changes issue state and records scope only.
- Stop condition: do not edit scoring until the issue comment names the trust
  defect and the exact non-live verification boundary.
