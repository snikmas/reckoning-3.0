# Phase 5: Reconcile #127 and close #136 truthfully

[Back to the overview](overview.md)

## Goal

Repair delivery history without rewriting what happened.

## TODO

- [ ] Land the #136 repair on `main`. A branch-only commit does not pass this
  phase.
- [ ] Run the documented fake command from the exact delivered revision.
- [ ] Add a #136 closure comment with the commit on `main`, changed contracts,
  focused and shared checks, fresh product counts, detector counts, limitations,
  and remaining Stage 2 gates.
- [ ] Check each #136 acceptance criterion separately. Do not close the issue if
  any required criterion is still partial.
- [ ] Keep live provider execution `unrun` unless Mary separately authorizes a
  bounded run.
- [ ] Add a reconciliation comment to #127. State that its original delivery
  was branch-only, that #136 and #137 supplied later on-main capability, and
  that the repaired #136 evidence does not prove Stage 2 or language-quality
  acceptance.
- [ ] Remove `partial` from #136 and close it only after the on-main evidence is
  linked.

## Files and records

- GitHub issues #127 and #136.
- The final implementation commits from phases 2 through 4.
- The fresh fake JSONL report. Keep temporary evidence outside the repository
  unless the issue contract explicitly requires a committed fixture.

## Data structures

Use the issue evidence matrix: criterion, status, code or test pointer, command,
result, and limitation.

## Verification

- Static: `git merge-base --is-ancestor <delivered-sha> origin/main` after the
  repair lands.
- Runtime: rerun the documented fake command from a clean checkout of the
  delivered revision.
- Stop condition: do not copy the historical 8/4/2 or Phase 1 9/3 counts as
  fresh results.
