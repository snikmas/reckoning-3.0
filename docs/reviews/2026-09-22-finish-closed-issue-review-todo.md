# Finish the closed-issue review

Created: 2026-09-22

## Completion record

The review ran against live `origin/main` `4487ae3` with 125 closed issues and
21 open issues at the frozen baseline. The delivered artifacts are:

- `2026-09-22-setup-credentials-migrations-review.md`.
- `2026-09-22-private-profile-review.md`.
- `2026-09-22-stage-2-post-delivery-review.md`.
- `2026-09-22-closed-issue-coverage-matrix.md`.
- the updated `2026-09-21-phase-2-post-repair-canonical-review.md`.

The review found and repaired one bounded rendered-web defect. On a narrow,
long conversation, the fixed composer covered the Inspector at maximum scroll,
and a successful send returned above the latest reply. The repaired page gives
mobile content enough bottom clearance and redirects successful sends to the
latest persisted reply.

The review also found that #137 is partial. The evaluator has aggregate
token-derived cost estimates and pre-call reservation, but it does not have the
per-attempt quote, actual provider-reported cost, and violation receipt required
by this TODO. Its issue body also permits acknowledged unknown-cost runs while
this TODO requires a positive cost ceiling. The Stage 2 review records both
alternatives without choosing a new product contract.

This TODO finishes the retrospective review that started with 121 closed issues.
The live repository now has 125 closed issues and 21 open issues. Local and
remote `main` both pointed to `4487ae3` when this TODO was created.

The work is complete only when every closed issue has a current evidence row,
all new findings have a truthful disposition, and the final review artifacts
are on `main`. A green test suite or a closed GitHub issue does not replace that
evidence.

## Preserve the current work

The working tree already contains these untracked artifacts:

- `docs/reviews/2026-09-21-phase-1-repair-plan/`
- `docs/reviews/2026-09-21-phase-2-post-repair-canonical-review.md`

Preserve both artifacts. Do not reset, stash, overwrite, or delete them. Review
their claims against current `main` before adding them to a commit. The Phase 2
report predates repairs #152 through #155, so update its findings and baseline
before treating it as final evidence.

Do not read `.env` files, credentials, private keys, or private persona source.
Use fake transports and temporary installations. Do not call a live or paid
provider without Mary's separate authorization and a stated cost limit.

Work in one agent context. Repository instructions prohibit subagents unless
Mary explicitly requests them.

## Outputs

Produce these review artifacts:

1. An updated Phase 2 report that records the post-repair state at current
   `origin/main`.
2. A setup, credentials, and migrations review for the remaining historical
   cluster.
3. A post-delivery Stage 2 review for the implemented conversation and live
   evaluation issues.
4. A final coverage matrix with one row for each closed issue.
5. GitHub evidence updates for closed issues whose bodies or closure records no
   longer describe the delivered state.

Keep implementation findings separate from product decisions. Fix a bounded
correctness defect only when the existing issue contract determines the
expected behavior. If a finding needs a product choice, record the alternatives
and stop for Mary's decision.

## Phase 1: freeze the review baseline

1. Run `git status --short --branch`.
2. Run `git ls-remote origin refs/heads/main`.
3. Record local `HEAD`, live `origin/main`, the issue counts, and the dirty
   paths in the review notes.
4. Export all closed issues with their number, title, state, labels, body,
   closure time, comments, and URL.
5. Confirm that the final matrix target is still 125. If the count changed,
   use the new count and explain why.

Use live GitHub state. Do not copy the September 21 counts into a new report.

Suggested inventory commands:

```bash
git status --short --branch
git rev-parse HEAD
git ls-remote origin refs/heads/main
gh issue list --repo snikmas/reckoning-3.0 --state closed --limit 500 --json number,title,state,labels,closedAt,url
gh issue list --repo snikmas/reckoning-3.0 --state open --limit 500 --json number,title,state,labels,updatedAt,url
```

Stop if local and remote `main` differ. Reconcile the intended review baseline
without changing or staging the existing untracked artifacts.

## Phase 2: review setup, credentials, and migrations

Review these issues as one subsystem:

- `#64-#65`
- `#68-#70`
- `#73-#75`
- `#80-#90`
- `#92`
- `#115`
- `#138`

The older reviews grouped some setup issues under superseding behavior. Do not
reuse a group verdict as an individual Spec verdict. For each issue, inspect the
issue body, closure evidence, implementing commits, current ancestry, current
code, and focused tests.

Check these risks directly:

- credential redaction, storage, replacement, and reset.
- provider verification and activation rollback.
- automatic processing-grant creation and later revocation.
- migration preview versus explicit mutation.
- repair behavior in `reckoning doctor`.
- installed-state detection and restart behavior.
- non-interactive setup parity.
- Telegram pairing and authorization.
- provider placement and availability truth.
- concurrent setup or stale-state behavior.
- unsupported providers remaining unavailable.
- clean-install behavior without existing local state.

Use these focused tests as the starting set. Add tests when an issue contract
names behavior outside this set.

```bash
RECKONING_LIVE_SMOKE=0 PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_provider_config.py \
  tests/test_provider_contract.py \
  tests/test_provider_key_verification.py \
  tests/test_provider_registry.py \
  tests/test_processing_grants.py \
  tests/test_setup_cli.py \
  tests/test_setup_terminal.py \
  tests/test_setup_workflow.py \
  tests/test_cloud_continuity_journey.py \
  tests/test_cloud_provider_run.py \
  tests/test_telegram_connector_setup.py \
  tests/test_recovery_contract.py
```

Do not enable OpenAI or a generic custom endpoint from deterministic adapter
tests. Provider availability requires an authorized installed journey against
the selected real destination.

Phase 2 is complete when every listed issue has separate Standards and Spec
verdicts, ancestry evidence, focused test evidence, and explicit unrun gates.

## Phase 3: close the privacy and authority gap

Review issue `#63`. The earlier canonical reviews covered the other issues in
the privacy and authority cluster but did not include `#63`.

The closure record names `bef9040` and reports 175 passing tests. Verify that
commit against current history instead of treating the old test count as
current acceptance evidence.

Trace the issue contract through current code and tests. Check optional Markdown
profile import, placement policy, provenance, unconfirmed bootstrap context,
record review, relevant confirmed retrieval, restart, backup, restore,
migration, correction, rejection, deletion, and no-profile behavior. Confirm
that profile text remains user data and cannot change instructions,
permissions, tools, or protected rules.

Start with:

```bash
RECKONING_LIVE_SMOKE=0 PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_user_profile_onboarding.py \
  tests/test_personal_context_persistence.py \
  tests/test_multilingual_personal_context.py \
  tests/test_context_lifecycle_and_maintenance.py \
  tests/test_recovery_contract.py
```

Do not expose private fixture content in the report. Use fictional public test
data.

Phase 3 is complete when `#63` has the same evidence fields and review depth as
the surrounding canonical issues.

## Phase 4: review delivered Stage 2 conversation work

Review these issues against current `main` as one conversation subsystem:

- `#124`
- `#134-#135`
- `#145-#147`

The implementation commits are present on `main`, but the retrospective review
does not yet map every contract to current behavior. GitHub issues `#134` and
`#135` are closed while their bodies still say `Not implemented` and lack final
closure evidence. Repair that history only after the current implementation
passes review.

Test through rendered or supported interfaces. Direct domain-service calls do
not prove the ordinary conversation journey.

Check these cases:

- a displayed proposal is bound to the confirmation target and revision.
- questions about confirmation never mutate state.
- retries preserve operation identity across response loss and restart.
- stale forms cannot confirm or correct a newer proposal.
- session creation, selection, resume, and restart keep transcripts separate.
- history selection keeps a contiguous suffix of complete turns.
- current input and protected context fit before any provider call.
- omission metadata stays outside assistant speech and stored assistant turns.
- protected-response and danger handling distinguish use, quotation,
  negation, history, and hypothetical discussion.
- web output remains escaped and does not accept model-generated HTML.
- narrow-screen and post-save failure behavior remain usable and truthful.

Use these focused tests as the starting set:

```bash
RECKONING_LIVE_SMOKE=0 PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_web_decision_journey.py \
  tests/test_decision_conversation.py \
  tests/test_decision_operations.py \
  tests/test_conversation_sessions.py \
  tests/test_conversation_composer.py \
  tests/test_context_budget.py \
  tests/test_protected_conversation_boundaries.py \
  tests/test_conversation_contract.py \
  tests/test_terminal_conversation.py \
  tests/test_interface_authority.py
```

Run real browser probes for displayed bindings, stale forms, reply rendering,
and the narrow layout. Record the browser, viewport, action, observed result,
and durable state evidence.

Phase 4 is complete when all six issues have current Standards and Spec
verdicts and truthful GitHub closure evidence.

## Phase 5: review live evaluation and spending truth

Review issue `#137` after Phase 4. The issue is closed but has no final closure
comment. Review the delivered implementation before adding one.

Verify these contracts with fake transports:

- the command requires a positive call limit and a positive cost limit.
- the runtime reserves the quoted cost before each request.
- the receipt records every completed call and its reported non-negative cost.
- a quote violation records actual cost and rejects the evaluated result.
- unknown prices and unknown actual costs remain unknown.
- provider failures, missing journeys, product failures, detector failures,
  and unauthorized runs remain distinct.
- the process stops before another call can exceed the remaining limit.
- JSONL records the runtime revision, dirty state, scenario digest, usage,
  spending, failures, and unrun human judgments.
- fake-provider success does not claim naturalness, usefulness, multilingual
  quality, or real-provider acceptance.

Start with:

```bash
RECKONING_LIVE_SMOKE=0 PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_conversation_evaluation.py \
  tests/test_bounded_delegation_enforcement.py \
  tests/test_cloud_provider_run.py
```

Do not run live evaluation as part of this TODO without separate authorization.
Record live provider behavior, monetary acceptance, and dated human language
judgments as `unrun` when no authorization exists.

Phase 5 is complete when `#137` has a current review verdict and its GitHub
history states what deterministic evidence proves and what remains unrun.

## Phase 6: update the Phase 2 findings after repairs

Recheck the four findings in
`docs/reviews/2026-09-21-phase-2-post-repair-canonical-review.md` against current
`main`:

1. connector revocation across stale instances.
2. the reproducible `#114` integration gate.
3. service-only watches, briefings, connectors, and delegation.
4. delegated cost after a quote violation.

Map the first, second, and fourth findings to delivered repairs `#152-#154`.
Map the service-only classification to `#155`. Rerun the adversarial probes and
the restored 16-phase gate. Keep service foundations classified as service
foundations. Do not promote them to user-facing features because their service
tests pass.

Run the checked-in gate directly:

```bash
PYTHONPATH=src .venv/bin/python docs/reviews/2026-09-12-issue-114-stage-gate-scenario.py
```

Update the report's baseline, counts, findings, and disposition. Preserve the
original finding text where it explains why each repair exists, but mark the
current result clearly.

## Phase 7: build the final closed-issue coverage matrix

Create `docs/reviews/2026-09-22-closed-issue-coverage-matrix.md` after Phases 2
through 6 pass.

Add exactly one row for every issue that is closed at the final baseline. Give
each row these fields:

- issue number and title.
- classification such as implementation, duplicate, superseded, prototype,
  investigation, stage gate, service foundation, waiver, or repair.
- canonical issue or replacement issue.
- source specification.
- closure evidence.
- implementing commit cited by the issue.
- patch-equivalent current commit when history changed.
- `origin/main` ancestry result.
- focused tests and adversarial probes.
- Standards verdict.
- Spec verdict.
- current disposition.
- skipped, excluded, manual, live, or unrun gates.

Do not collapse duplicate issues into one row. Give each duplicate its own row
and route it to the canonical review. Do not count an investigation, prototype,
waiver, or service foundation as a delivered user capability.

Add a reconciliation section with these checks:

- matrix row count equals the live closed-issue count.
- every closed issue number appears exactly once.
- no open issue appears as accepted delivery.
- every cited commit either belongs to current `origin/main` ancestry or has a
  named patch-equivalent replacement.
- every failed or partial verdict has a disposition.
- every live, manual, paid, platform-specific, or human-language gate is
  recorded as passed, failed, blocked, or unrun.
- issues `#152-#155` appear as the repair records for the Phase 2 findings.

The 21 open issues remain outside the retrospective acceptance set. They may
appear only as dependencies, remaining product work, or acceptance owners.

## Phase 8: run the final verification gate

Run the focused suites from each phase before the full gate. Then run:

```bash
RECKONING_LIVE_SMOKE=0 PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/python -m mypy --python-version 3.13 --ignore-missing-imports src
.venv/bin/python -m ruff check --select E4,E7,E9,F src tests
.venv/bin/python -m compileall -q src tests
git diff --check
PYTHONPATH=src .venv/bin/python docs/reviews/2026-09-12-issue-114-stage-gate-scenario.py
```

Report passed, failed, skipped, deselected, excluded, manual, and unrun checks
separately. Do not convert a skipped or unavailable check into a pass.

If a test fails, determine whether the failure is a product regression, a stale
test, an environment problem, or a known unsupported gate. Do not change code
until the cause is clear.

## Phase 9: deliver the review

Before committing, inspect the complete diff. Stage only the reviewed files.
Do not absorb unrelated work.

The delivery commit must include:

- the corrected existing review artifacts.
- the new setup and Stage 2 review reports.
- the final closed-issue coverage matrix.
- tests and product repairs that were required by accepted issue contracts.
- no credentials, private persona material, generated evaluation output with
  private content, or unrelated files.

Push only after the exact committed tree passes the final gate. Then update the
affected GitHub issues with the delivered revision, commands, counts, findings,
limitations, and unrun gates. Do not close an issue whose acceptance criteria
remain partial.

## Definition of done

This TODO is complete when all of these statements are true:

- Local `main` and `origin/main` contain the delivered review artifacts.
- The working tree is clean, except for unrelated user work named in the final
  report.
- Every closed issue has exactly one current coverage row.
- The setup, credentials, and migrations cluster has a deep subsystem review.
- Issue `#63` has a current privacy and authority review.
- Issues `#124`, `#134-#135`, and `#145-#147` have a post-delivery Stage 2
  review.
- Issue `#137` has a post-delivery spending and accounting review.
- Phase 2 findings are marked resolved by `#152-#155` or retain a truthful
  partial disposition.
- GitHub closure evidence matches current `main`.
- The final verification report separates passes, failures, skips, exclusions,
  manual checks, and unrun gates.
- No live or paid provider call occurred without separate authorization.

Stop and ask Mary when a finding requires a new product decision, live-provider
authorization, access to private persona material, or destructive handling of
unrelated work.
