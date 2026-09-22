# Phase 1 repair plan

> Historical plan status, reviewed 2026-09-22: this directory records the plan
> used before the repairs landed. Its unchecked boxes are not current product
> status. #79 and #136 were repaired and closed with on-main evidence; #127 and
> the setup supersession issues received reconciliation comments. Current
> acceptance and remaining limits are recorded in the 2026-09-22 closed-issue
> coverage matrix and Stage 2 post-delivery review.

This plan turns the Phase 1 review findings into small, reviewable repairs. It
does not implement product code, change GitHub issues, or run live providers.
The working baseline is local `main` at `d999358`, one documentation commit
ahead of `origin/main` at `1d9ea347`.

## Scope

The plan covers the blocking trust and delivery gaps in #136 and #127, the
current README drift found through #79, and the unresolved historical meaning
of #67, #71, #76, #78, and #82. It preserves the replacement setup design from
#80, #89, and #101. It does not restore removed setup modes, unavailable
connector rows, the old provider-management menu, or the old Rich setup
renderer.

Stage 2 remains governed by #122 and its open delivery issues. Repairing these
historical records does not close #122 or prove language quality, usefulness,
live-provider acceptance, Telegram continuity, or the remaining Stage 2
journeys.

## Recommended decisions

- Reopen #136. Mark it `partial` until the scoring trust boundary, evaluator
  documentation, fresh fake report, and on-main closure evidence are complete.
- Keep #127 closed as a historical record. Add a final reconciliation comment
  only after #136 has trustworthy on-main evidence.
- Reopen #79 for the current README defect. Do not restore its obsolete 0.2
  migration table because #80 explicitly removed that documentation.
- Record #67, #71, #76, #78, and #82 as historically delivered and later
  superseded in part. Do not reopen them unless Mary rejects the named newer
  product decision.
- For #71, accept the #80 and #89 status-and-edit hub as the current design.
  Do not restore a multi-provider management menu without a current user need.

## Constraints

- Work single-agent. Repository instructions prohibit subagents unless Mary
  asks for them.
- Use existing GitHub issues. Do not create replacement repair issues.
- Preserve unrelated work and the local Phase 0 inventory commit.
- Do not read private configuration, credentials, or `.env` files.
- Use fake transports and temporary installations. Do not call a live or paid
  provider without separate explicit authorization.
- Keep product reports separate from detector-test success.
- A commit, closed issue, or green unit suite is not acceptance evidence by
  itself. The delivered revision must be on `main`, and the runtime result must
  match the claim.

The Poteto planning playbook normally delegates repository exploration. That
step was skipped because this repository explicitly prohibits subagents. The
issue bodies, current code, help output, commit ancestry, and test entry points
were inspected directly instead.

## Alternatives considered

1. Restore every old acceptance criterion literally. Rejected. This would
   reintroduce setup behavior that #80 and #101 deliberately removed.
2. Create new repair issues for every finding. Rejected. The repository says
   to repair existing issue history instead of creating duplicates.
3. Repair the current contract and annotate supersession. Chosen. It fixes real
   regressions, preserves historical truth, and keeps the current product
   design coherent.

## Ordered TODOs

1. [Reopen #136 and freeze the repair contract](phase-1-reopen-and-freeze-contract.md).
2. [Remove expected-result influence from evaluator scoring](phase-2-evaluator-trust-boundary.md).
3. [Add failure probes through the real web journey](phase-3-evaluator-failure-probes.md).
4. [Restore runnable evaluator documentation](phase-4-document-evaluator.md).
5. [Reconcile #127 and close #136 truthfully](phase-5-reconcile-127-and-136.md).
6. [Repair README drift under #79](phase-6-repair-readme.md).
7. [Record setup supersession decisions](phase-7-record-setup-supersession.md).
8. [Run the final acceptance matrix](phase-8-final-acceptance.md).

The throughput checkpoint is after phase 5. Do not spend time on the historical
setup comments until the evaluator can produce a trustworthy, documented report
from an on-main revision.

## Definition of done

- #136's runtime score depends only on observed output, authoritative state
  evidence, and a versioned evaluator rule. Changing a fixture expectation
  alone cannot change the computed score.
- Detector tests can pass while the deliberately bad product case remains
  failed in the product report.
- The documented fake command works from the delivered revision and writes
  append-only JSONL. Missing, failed, partial, and unrun mandatory cases keep a
  nonzero exit status.
- #136 has a closure comment with the on-main revision, fresh commands, counts,
  limitations, and remaining gates.
- #127 explains that its original branch-only delivery failed the repository
  gate and points to the later accepted replacement without claiming Stage 2
  completion.
- README lists every top-level command shown by `reckoning --help`, including
  `reckoning evaluate`, and an automated check catches future command-table
  drift.
- #67, #71, #76, #78, and #82 each name the later issue that replaced the old
  interaction and the requirements that remain valid.
- The final matrix reports passed, failed, skipped, and unrun checks separately.

## Implementation guidance

- Use Poteto Mode's **how** workflow before changing evaluator scoring or the
  web driver.
- Use **Prove It Works** on the CLI and rendered web journey, not only helper
  functions.
- Use **Sequence Work into Verifiable Units**. Land the scorer, failure probes,
  docs, README, and metadata reconciliation as separate reviewed units.
- Apply **Redesign from First Principles** to the scorer. Expected outcomes are
  test or benchmark data, not scoring inputs.
- Run `/deslop` before each commit and apply `unslop` to documentation and issue
  comments.
- Keep a local decision trail with `show-me-your-work` during implementation.
- After opening a pull request, use the repository's normal pre-landing review
  and babysitting flow. Do not use the full historical `/code-review` workflow
  across all eight issues.

See [testing.md](testing.md) for the shared verification commands and runtime
checks.
