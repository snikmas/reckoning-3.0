# Phase 4: Restore runnable evaluator documentation

[Back to the overview](overview.md)

## Goal

Give a maintainer one current command that produces understandable, truthful
evaluation evidence from the delivered revision.

## TODO

- [ ] Add `docs/evaluations/conversation-quality.md`.
- [ ] Document the scenario path as
  `scenarios/stage2-conversation-v1.json`.
- [ ] Document a fake command with explicit `--scenario-set`, `--output`, and
  `--profile` arguments.
- [ ] Explain append-only JSONL, runtime revision and dirty state, profiles,
  digests, product counts, detector counts, and nonzero exit semantics.
- [ ] Explain why fake output cannot prove naturalness, usefulness, provider
  quality, monetary cost, or Stage 2 acceptance.
- [ ] Document the separate authorization and budget requirements for live mode
  without running it.
- [ ] Add a documentation-path test or link check so a later scenario move does
  not leave a broken command.

## Files

- `docs/evaluations/conversation-quality.md`.
- `tests/test_conversation_evaluation.py` or the repository's existing docs
  link test.

## Data structures

Document one JSONL record and one run summary. Do not copy personal prompts,
credentials, or real provider output into the repository.

## Verification

- Static: link and command-path checks, `git diff --check`, and an unslop pass.
- Runtime: run the documented fake command exactly as written in a fresh
  temporary directory. Record its nonzero exit as expected when mandatory cases
  remain missing or failed.
