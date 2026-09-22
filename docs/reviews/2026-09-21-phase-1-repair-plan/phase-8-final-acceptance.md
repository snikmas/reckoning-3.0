# Phase 8: Run the final acceptance matrix

[Back to the overview](overview.md)

## Goal

Finish with one current record that separates code health, evaluator behavior,
documentation, issue history, and unrun external gates.

## TODO

- [ ] Confirm the working tree contains only the intended repair commits and
  preserves the Phase 0 inventory.
- [ ] Run the focused evaluator, web-journey, and CLI documentation tests.
- [ ] Run the non-live repository suite, mypy, Ruff, compileall, and
  `git diff --check`.
- [ ] Run the documented fake `early-web` and `full-stage-2` profiles. Preserve
  their distinct product, missing, detector, and excluded counts.
- [ ] Smoke the fake-provider web flow in a browser at a narrow and normal
  viewport.
- [ ] Verify the #79, #127, and #136 issue comments against the landed commits.
- [ ] Verify the five supersession comments link the correct replacement
  issues.
- [ ] Report live providers, paid judging, macOS, WSL, and unfinished Stage 2
  journeys as `unrun` or remaining gates. Do not group them with passing checks.

## Files and records

- [testing.md](testing.md).
- GitHub issues #67, #71, #76, #78, #79, #82, #127, and #136.
- The final repair commits and fake evaluation output.

## Data structures

Use one final matrix with these columns: area, artifact, command or observation,
passed, failed, skipped, unrun, and implication.

## Verification

- Static and runtime checks are listed in [testing.md](testing.md).
- Final gate: an independent reviewer can reproduce the README and evaluator
  claims from a clean checkout without credentials or private data.
