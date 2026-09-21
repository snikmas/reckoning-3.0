# Phase 1 review: canonical issues #33-#47

Review date: 2026-09-21

Baseline: `d999358` on local `main`. Product code matches `origin/main` at
`1d9ea34`; the only additional commit is the Phase 0 inventory.

Scope: canonical implementation issues #33-#47, excluding #39 because Phase 0
classified it as a product-evidence gate. This review checks the original
delivery changes and whether their contracts remain true on the current tree.

This repository forbids subagents, so the Standards and Spec axes were reviewed
separately in one context.

## Fixed points and delivery diffs

| Issues | Delivery commits | Review diff |
| --- | --- | --- |
| #33 | Current-history root `6386160`; issue receipt `9afb95f` is no longer available | Empty tree to `6386160` |
| #34 | `597b395` | `git diff 6386160..597b395` |
| #35 | `bc4b0de` | `git diff 597b395..bc4b0de` |
| #36 | `7756fff` | `git diff bc4b0de..7756fff` |
| #37 | `ad59fba`, `fd36e4b` | `git diff 7756fff..fd36e4b` |
| #38 | `0b306ec`, `548732c`, `3b44dd8` | Each delivery commit against its first parent |
| #40-#42 | `f85c516` | `git diff 878ac63..f85c516` |
| #43-#44 | `bf6fb91` | `git diff f85c516..bf6fb91` |
| #45-#47 | `b8411c5` | `git diff bf6fb91..b8411c5` |

## Standards

No hard violation of a documented repository coding standard was found. The
repository documents issue, evidence, and domain-language rules, but no separate
code-style standard beyond enforced mypy and Ruff checks.

One Fowler-baseline judgement finding remains:

1. **Possible Speculative Generality.** `planning.py`,
   `research_forecasts.py`, and `memory_maintenance.py` expose substantial
   service and repository abstractions, but production code never imports their
   services. Only focused unit tests call them. This is more than unused cleanup:
   the originating issues describe user capabilities, while the abstractions
   stop before an application boundary or durable production repository.

## Spec

### Findings

1. **[P1] Permanent deletion leaves derived personal content in maintenance summaries.**
   Spec: "Permanent deletion removes content and links, retains only a
   content-free suppression marker, and prevents later recreation through
   consolidation."

   Issue #41 requires deletion to remove content and links while retaining only a
   content-free suppression marker. `InMemoryMaintenanceRepository.remove_context_links()`
   removes record IDs and index entries but copies `result.summary` unchanged at
   `src/reckoning/memory_maintenance.py:77-96`. A synthetic summary containing the
   deleted fact remained byte-for-byte after `PersonalContextService.delete()`.
   The existing test checks only IDs and index entries.

2. **[P1] Forgotten context is still sent to the maintenance summarizer.**
   Spec: "Forgotten content is excluded from retrieval while remaining
   recoverable."

   Issue #41 says forgetting excludes content from retrieval, and #42 makes
   maintenance part of governed recall. `_current_sources()` at
   `src/reckoning/memory_maintenance.py:198-202` selects the latest version of
   every record without checking `active`, `retrieval_permitted`, freshness, or
   placement. A forgotten synthetic record reached the summarizer in the probe.

3. **[P1] Correcting forgotten or archived context silently recovers it.**
   Spec: "The user can apply and verify distinct archive, forget, retention,
   and permanent-deletion behavior to personal context."

   Issue #41 defines recovery as a distinct operation. `correct()` at
   `src/reckoning/personal_context.py:831-856` assigns `active` to every
   non-proposed record. A direct correction to a forgotten record therefore
   makes it retrievable without `recover()`.

4. **[P1] #40-#47 were closed as user capabilities, but most stop at service-level code.**
   Specs include "The user explicitly remembers... and later retrieves only the
   current meaning" (#40), "The user manages distinct direction and goal
   lifecycles" (#43), and "The user requests one bounded research task and
   receives an inspectable conclusion" (#45).

   Production code has no caller for `MemoryMaintenanceService`,
   `GoalPlanningService`, `PlanService`, `BoundedResearchService`, or
   `ForecastService`. Planning and research have only in-memory repositories.
   Personal context is used for setup/profile proposals and conversation
   retrieval, but the product exposes no ordinary remember, archive, forget, or
   recover path. The direct-service unit tests prove algorithms, not that a user
   can complete the issue journeys or that planning, research, and forecasts
   survive restart.

5. **[P2] The first-reckoning validator accepts an empty result.**
   Spec: "The reckoning distinguishes what matters now, what stays maintained,
   what can be parked, and what remains uncertain" and "The answer separates
   knowledge from inference."

   Issue #35 requires the result to distinguish matters now, maintained work,
   parked work, and uncertainty, and to separate knowledge from inference.
   `ReckoningDraft.validate()` at `src/reckoning/continuity.py:80-104` validates
   conflict, next step, question count, and proposed-record evidence only. A
   draft with every required bucket, fact, inference, and evidence collection
   empty passes validation.

6. **[P2] Negative research cost bypasses the bounded-cost contract.**
   Spec: "The user requests one bounded research task" and the conclusion cites
   source "cost."

   Issue #45 requires bounded cost. `BoundedResearchService.complete()` sums
   `cost_units` at `src/reckoning/research_forecasts.py:208-210` without rejecting
   negative values. A source with `cost_units=-10` completed a request whose
   maximum cost was zero.

7. **[P2] The planning type contract omits required lifecycle states.**
   Parent spec: "Directions [are] active, maintained, parked, or stopped" and
   goals are "proposed, active, paused, blocked, completed, or abandoned."

   Parent specification #32 requires directions to support `active`,
   `maintained`, `parked`, and `stopped`, and goals to support `proposed`,
   `active`, `paused`, `blocked`, `completed`, and `abandoned`. The public type
   aliases at `src/reckoning/planning.py:7-8` omit `maintained`, `parked`,
   `stopped`, and `blocked`, while adding `exploring` and `paused` for directions.
   No runtime transition validation compensates for the mismatch.

### Issue coverage matrix

| Issue | Spec source and closure evidence | Implementing commit and ancestry | Current focused evidence | Result |
| --- | --- | --- | --- | --- |
| #33 | Issue body; closure receipt names missing `9afb95f` | Equivalent root `6386160` is on current `main` | First-session web journey passes | Pass, with historical SHA loss recorded in Phase 0 |
| #34 | Issue body; closure receipt names `597b395` | `597b395` is on current `main`; later protected-boundary repairs also landed | Protected-persona tests pass | Pass |
| #35 | Issue body; closure receipt names `bc4b0de` | `bc4b0de` is on current `main` | First-reckoning tests pass; empty-bucket probe fails | Partial, finding 5 |
| #36 | Issue body; closure receipt names `7756fff` | `7756fff` is on current `main` | Correction, confirmation, and Why tests pass | Pass |
| #37 | Issue body; closure receipts name `ad59fba` and `fd36e4b` | Both are on current `main`; persistence was later moved to SQLite | Restart/resume/check-in tests pass | Pass |
| #38 | Issue body; adapter and live-acceptance comments | `0b306ec`, `548732c`, and `3b44dd8` are on current `main` | Provider/config/continuity tests pass; historical live DeepSeek evidence exists | Pass on deterministic and historical evidence; no live call rerun |
| #40 | Issue body; closure receipt names `f85c516` | `f85c516` is on current `main`; context storage later became transactional | Multilingual correction and retrieval-filter tests pass | Partial, no ordinary remember journey |
| #41 | Issue body; closure receipt names `f85c516` | `f85c516` is on current `main` | Positive lifecycle tests pass; three adversarial probes fail | Partial, findings 1-3 and 4 |
| #42 | Issue body; closure receipt names `f85c516` | `f85c516` is on current `main` | Positive maintenance/pattern tests pass; forgotten-source probe fails | Partial, findings 1, 2, and 4 |
| #43 | Issue body; closure receipt names `bf6fb91` | `bf6fb91` is on current `main` | Direct service tests pass | Partial, findings 4 and 7 |
| #44 | Issue body; closure receipt names `bf6fb91` | `bf6fb91` is on current `main` | Direct plan/check-in tests pass | Partial, finding 4 |
| #45 | Issue body; closure receipt names `b8411c5` | `b8411c5` is on current `main` | Positive bounded-research tests pass; negative-cost probe fails | Partial, findings 4 and 6 |
| #46 | Issue body; closure receipt names `b8411c5` | `b8411c5` is on current `main` | Direct forecast validation tests pass | Partial, finding 4 |
| #47 | Issue body; closure receipt names `b8411c5` | `b8411c5` is on current `main` | Direct revision-state tests pass | Partial, finding 4 |

## Verification

- Focused canonical suite: `54 passed`.
- Full current suite: `842 passed, 15 skipped`.
- Mypy: clean on 48 source files.
- Ruff E4/E7/E9/F: clean.
- Adversarial probe file outside the repository: `5 failed`, one for each
  reproduced behavior in findings 1, 2, 3, 5, and 6.
- Live provider calls: unrun. This review used the historical #38 acceptance
  receipt and current deterministic adapter tests.

## Disposition

Do not reopen #33-#47 as a batch. Findings 1-3 belong in one bounded
personal-context/maintenance repair because they share the deletion and lifecycle
boundary. Finding 5 belongs in the existing first-reckoning validation contract.
Finding 6 is a small bounded-research validation repair. Findings 4 and 7 require
a product decision before implementation: either make these capabilities durable
and reachable through the current product boundary, or explicitly reclassify the
old issues as prototypes/service foundations rather than completed user journeys.

Summary: Standards has one judgement-call finding and no hard documented
violations. Spec has seven findings; the worst are the P1 deletion/forget
failures and the missing production journey for #40-#47.
