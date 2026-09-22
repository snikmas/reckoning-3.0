# Phase 6: Repair README drift under #79

[Back to the overview](overview.md)

## Goal

Make README match the current command surface without reviving obsolete 0.2
migration guidance.

## TODO

- [ ] Reopen #79 and add `partial` while the current documentation defect is
  active.
- [ ] Reduce the Quickstart code block to four command lines. Keep the optional
  Gateway command in the command table and task-based guidance.
- [ ] Add `reckoning evaluate` to the Run group with the same description as
  top-level help.
- [ ] Add the evaluation use case to "Which command when" and link the new
  evaluation guide.
- [ ] Keep version metadata at `0.2.0`.
- [ ] Do not restore the old "Commands changed in 0.2.0" table. Add a #79 issue
  comment that records #80's explicit supersession of obsolete command-history
  documentation.
- [ ] Add a focused test that compares the README command rows with the public
  top-level command registry or rendered help.
- [ ] Close #79 only after the README test and shared documentation checks pass
  on `main`.

## Files

- `README.md`.
- `tests/test_main_cli.py` or the nearest existing command-help test.
- GitHub issue #79.

## Data structures

Treat the public command registry as the source set. The README table is a
documented projection of that set, not an independently maintained command
list.

## Verification

- Static: README/help consistency test, Ruff if the test changes Python, and
  `git diff --check`.
- Runtime: compare `PYTHONPATH=src python -m reckoning --help` with the README
  table and run the documented evaluate help command.
