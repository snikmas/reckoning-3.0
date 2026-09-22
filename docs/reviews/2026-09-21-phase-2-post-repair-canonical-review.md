# Phase 2 review: canonical issues #48-#123 after repairs

Review date: 2026-09-21, updated 2026-09-22 after #152-#155

Updated baseline: `4487ae3` on local `main`, matching live `origin/main` when
the follow-up review started. The original findings below are retained because
they explain why #152-#155 exist. Each heading now states the current result.

Inputs:

- [Phase 0 historical issue inventory](2026-09-21-phase-0-historical-issue-inventory.md)
- [Phase 1 canonical review](2026-09-21-phase-1-canonical-33-47-review.md)
- Current GitHub issue bodies, comments, labels, and closure state
- Current repository code, tests, CLI behavior, and browser behavior

Scope follows the requested routing exactly: #48-#60 are canonical; #61 and
#62 are evidence records; #66 is reviewed with #67-#71; #72 is reviewed with
#73-#79, with #79 counted once; #80 and #89 replace obsolete #91; #105-#112
replace investigation #104; #117-#119 replace diagnostic #113; #114 is rerun
as an integration gate; and #123 is classified as a prototype rather than
production Stage 2 evidence.

This repository forbids subagents, so the review was completed in one context.

## Fixed points and current-history replacements

History was rewritten after some original issue receipts were recorded. The
current equivalents used for this audit are:

| Issues | Current-history delivery commits |
| --- | --- |
| #48-#62 | `9a36e7e`, `95a6ce3` |
| #67-#71 | `336a391`, `dfed1e0`, `28c0faa`, `f4c9a4c`, `ca3c880`, `55033b1` |
| #73-#79 | `6067cfc`; README repair `c3c2dda` |
| #80 | `947b388` |
| #89 and #94 | `065481f`, `7b0407c`, `048759c` |
| #105 | `d1e8987`, `427a739` |
| #106 | `5a8d7b2` |
| #107-#110 and #112 | `8f575b3` |
| #111 | `4f3fd0d` |
| #117-#119 | `6cb697f`, with `5a7d4e5`, `df22791`, `3e33d53` |
| #114 | `ad13a00` |
| #152 | `8a5b7f8`, `d73f732` |
| #153 | `70cf1a7`, `5305e26`, `0e34780` |
| #154 | `9f5e65f` |
| #155 | GitHub classification record; delivery merged at `4487ae3` |

## Standards

No hard violation of a documented repository coding standard was found. Mypy,
Ruff, compilation, product-spec checks, questionnaire checks, and diff checks
are clean.

One recurring design concern remains: several historical user-capability issues
are implemented as isolated services exercised only by tests. That is a spec and
product-boundary problem rather than a style violation.

## Spec findings

### 1. Resolved by #152: stale connector revocation

Issue #52 requires revocation and disconnection to stop future access.
`ConnectorService` loads the entire JSON document into memory at construction,
checks only that cached connection before synchronization, and rewrites the
entire document on every flush (`src/reckoning/connectors.py:114-158`,
`182-234`, and `311-330`).

An adversarial two-instance probe reproduced this sequence:

1. Instances A and B connect the same read connector.
2. A revokes it; a fresh reader reports `revoked`.
3. Stale B synchronizes using its cached `connected` state.
4. A fresh reader reports `healthy`, and B has read the source after revocation.

The positive single-instance tests pass, but the cross-process authority
contract does not. Revocation needs authoritative compare-and-swap storage or a
fresh-state check immediately before every read, plus a two-instance regression
test.

Current result: `ConnectorService` serializes persisted mutations with a
per-store cross-process lock and reloads the authoritative snapshot before a
read. Two-instance regressions cover both revoked and disconnected terminal
states. The stale adapter is not called and a fresh instance retains the
terminal state. #152 and PR #156 resolve this finding on current `main`.

### 2. Resolved by #153: reproducible #114 gate

The #114 closure receipt points to:

- `docs/reviews/2026-09-13-issue-114-stage-gate-evidence.md`
- `docs/reviews/2026-09-12-issue-114-stage-gate-scenario.py`

Neither file exists on current `main`. Both can be recovered from historical
object `c96432c`, but the exact historical scenario now fails with
`AttributeError: 'str' object has no attribute 'speech'`. Its test responder
returns the old string response type, while the current interface contract
expects `ChannelResponse`.

A temporary, uncommitted compatibility substitution that changed only the
historical responder to return `ChannelResponse` passed all 16 scenario phases
against `4bf73bb`. This is useful current product evidence, but it is not an
exact rerun of the checked-in gate because the checked-in gate is missing and
stale. Restore and update the scenario and its evidence record before treating
#114 as a reproducible release gate.

Current result: #153 restored both files, updated the responder to the current
`ChannelResponse` contract, and refreshed the evidence. The checked-in command
now passes all 16 phases directly on `4487ae3` without a temporary source
rewrite.

### 3. Classified by #155: #50, #51, #52, and #60 are service foundations

The current tree has direct-service implementations and tests for watches,
briefings, read connectors, and delegation. Production code reads watch and
briefing files for operational summaries, but no supported CLI, web, Telegram,
or scheduler path creates or runs those services. `DelegationCoordinator` and
`ConnectorService` also have no production caller; their callers are tests.

The algorithms are substantial and their focused tests pass, but they do not
prove that a user can configure, execute, inspect, revoke, or recover these
capabilities through the product. These issues should be classified as service
foundations until an installed writer/executor and an end-to-end journey exist.
That classification also prevents their direct-service receipts from being
misread as Stage 2 production evidence.

Current result: #155 records that classification on #50, #51, #52, and #60.
#61 and #62 remain historical evidence records, #123 remains a prototype, and
#94 remains unavailable pending an authorized installed journey. No new user
capability was added.

### 4. Resolved by #154: delegated over-quote cost accounting

Issue #60 requires bounded cost and an inspectable receipt. The runtime counts a
model call before execution, but raises when the response cost exceeds the quote
before adding that cost to `runtime.cost` (`src/reckoning/delegation.py:292-305`).
The coordinator then copies `runtime.cost` into the receipt
(`src/reckoning/delegation.py:475-521`, `549-565`).

A gateway that quoted `0.10` and returned an actual cost of `0.20` produced a
limited execution with one model call and recorded cost `0`. The failure closes
acceptance of the delegated answer, but it does not make the external spend
zero. The receipt must record the actual reported cost, while separately
recording the quote violation.

Current result: #154 records the returned non-negative cost before checking the
quote. The regression now records one model call, cost `0.20`, a separate
quote-exceeded violation, and a limited result. Pre-call quote enforcement is
unchanged.

## Canonical issue disposition

| Record | Current evidence | Disposition |
| --- | --- | --- |
| #48 | Routine proposal, confirmation, material-change, scope, schedule, delivery, budget, and provider tests pass | Pass |
| #49 | SQLite routine runs, claims, retries, idempotency, and receipts pass across instances | Pass after #118 |
| #50 | Watch trigger, source scope, threshold, budget, notification, and suppression tests pass | Service foundation under #155; not a user-facing feature |
| #51 | Briefing ranking, cap, explanation, delivery, and no-change receipt tests pass | Service foundation under #155; not a user-facing feature |
| #52 | Connector lifecycle, permission, deletion, prompt-injection, and stale-instance terminal-state tests pass | Service foundation under #155; correctness defect resolved by #152 |
| #53 | External-write approval, standing permission, revocation, idempotency, and receipt tests pass across instances | Pass after #119 |
| #54 | External-content boundary and adversarial-content tests pass | Pass |
| #55 | Home/task-area state tests pass; real Chromium at 390 by 844 had no horizontal overflow and kept navigation, composer, and inspector available | Pass |
| #56 | Control and operational-record truth tests pass | Pass |
| #57 | Telegram setup, authorization, reservation, recovery, and durable delivery tests pass | Pass on deterministic evidence; no live Telegram rerun |
| #58 | Interface placement and session continuity tests pass | Pass |
| #59 | Backup, restore, migration, and recovery-contract tests pass | Pass |
| #60 | Delegation scope, processing grants, host limits, evidence review, receipts, and actual over-quote cost pass | Service foundation under #155; correctness defect resolved by #154 |
| #61 | Historical clean-install record; its issue history also marks it duplicate evidence for #30 | Evidence record only, not current product acceptance |
| #62 | Historical persona record; its issue history marks it duplicate evidence for #31 | Evidence record only, not current product acceptance |
| #66 with #67-#71 | Historical setup iteration; later comments explicitly supersede disabled connector rows and the old management loop | Reviewed as one historical group; current behavior is judged under #80 and #89 |
| #72 with #73-#79 | Historical unified-CLI iteration; later #80 behavior supersedes parts of #76 and #78; #79 README drift is repaired by `c3c2dda` | Reviewed once as one historical group; no second #79 review |
| #80 and #89 | Guided terminal onboarding, installed state, runtime setup, help, and status tests pass; PTY arrow-key subset passes | Pass on deterministic evidence; no paid-provider rerun |
| #94 | Candidate adapters and protocol tests pass; unavailable entries remain gated | Keep unavailable; see provider decision below |
| #105-#112 | Transactional receipts, authoritative context, continuity transactions, processing grants, browser mutation policy, visible replies, durable Telegram delivery, and trial decision tests pass | Pass after the repair issues; #104 is not used as acceptance evidence |
| #117-#119 | Independent-instance channel state, routine authority, and external-write revocation tests pass | Pass after the repair issues; #113 is not used as acceptance evidence |
| #114 | The restored checked-in scenario passes all 16 phases directly | Reproducible gate restored by #153; external gates remain unrun |
| #123 | Branch-only simulated dashboard was accepted as a visual prototype and is not on `main` | Prototype only; not production Stage 2 evidence |

Obsolete #91, investigation #104, and diagnostic #113 were not substituted back
into the acceptance set.

## #94 provider decision

OpenAI and the generic custom endpoint should remain unavailable for now. More
generic adapter implementation is not the missing piece.

The OpenAI candidate already targets the official HTTPS host, uses Chat
Completions, discovers models, redacts secrets, and defaults to
`gpt-5.6-terra`. Current official OpenAI documentation still lists
`gpt-5.6-terra` and Chat Completions support:
<https://developers.openai.com/api/docs/models/gpt-5.6-terra>. The repository's
own contract deliberately keeps OpenAI unavailable until an opt-in real smoke
passes (`src/reckoning/provider_registry.py:101-114` and
`tests/test_provider_contract.py:531-573`). No API key, paid call, or live
provider authorization was supplied for this audit.

Enabling OpenAI therefore requires a separately authorized, bounded live run
that covers setup, key verification, activation, an ordinary conversation,
receipt/status truth, restart, and repair. One direct HTTP completion is not
enough.

The custom endpoint is endpoint-dependent: one test server cannot prove every
OpenAI-compatible implementation. Keep it gated until the product defines
availability as per-endpoint verification and tests a selected real endpoint
through the same installed journey. Do not mark a universal custom provider
available from stubbed protocol tests.

## #114 gate evidence

Current non-live verification on `4487ae3`:

- Full suite recorded by #153 after merge: `863 passed, 15 skipped`.
- Mypy: clean on 48 source files.
- Ruff E4/E7/E9/F: clean.
- PTY arrow-key/setup subset: `5 passed, 29 deselected`.
- Compilation and `git diff --check`: pass.
- Checked-in #114 scenario: all 16 phases pass directly.
- Product-spec and grilling scripts: fail because their three required
  documentation inputs are absent from current `main`; these remain recorded
  failures rather than being converted to passes.
- Live paid providers and live Telegram: unrun.

Skipped tests remain skipped evidence, not passes. The full green suite does not
erase findings 1 and 4 because both were reproduced by adversarial probes
outside the existing positive cases.

## Disposition

All four original findings now have truthful dispositions. #152 fixes connector
authority, #153 restores the gate, #154 fixes delegated actual-cost accounting,
and #155 records the service-foundation decision. #94 still needs separate
authorization, credentials, and a bounded installed-provider run before it can
be enabled. #123 remains outside production Stage 2 evidence.

Summary: the four blocking findings from the original report are resolved or
classified on current `main`. Passing service tests still do not promote
watches, briefings, connectors, or delegation into user-facing capabilities.
