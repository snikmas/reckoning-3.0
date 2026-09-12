# Issue #114 stage-gate evidence: the repaired installation

Date: 2026-09-13. Verifier: one agent, same context. Synthetic data and fake
transports throughout; no secrets read, no real private state migrated, no
messages sent, no paid requests made.

## Baseline and dependency verification

- Starting checkout: `main` at `7244301` (equal to `origin/main`), clean
  working tree.
- Every #114 blocker was verified against its posted acceptance evidence, not
  its closed label. #103, #106, #107, #109, #110, #111, #112, and #113 each
  carry a closing evidence comment naming the implementing commit and test
  results, and those commits are ancestors of the starting checkout.
- **Finding:** #117, #118, and #119 posted evidence on branch
  `codex/trust-10-shared-state` (PR #120), but PR #120 was still open — the
  fixes were not on `main`. The #113 probe rerun on `7244301` reproduced all
  three failures (one lost channel session, one reverted routine confirmation,
  lost prepared write/approval, revoked permission restored).
- **Resolution:** merged `codex/trust-10-shared-state` into `main` as
  `8f2a482`, resolving one conflict in `model_run_store.py` by keeping both
  the Telegram (#111) and the external-write/interface/automation schema
  validators. The merge completes PR #120.
- **Post-merge repairs needed for a green gate:** the branch predated the
  #111 Telegram store, so `tests/test_setup_workflow.py` migration
  interruption tests assumed no store-authority write during migration
  inspection. Coverage was remapped to the new write sequence (commit-phase
  interruptions at replace 2–4; a new test pins the fail-safe
  inspection-phase interruption at replace 1). 27 mypy errors and 1 ruff
  F401 introduced by the branch were fixed (typed run-claim record replacing
  `list[object]`, optional-filtering list reads, `Literal` casts,
  repository protocol annotation, `int(str(...))` grant version coercion).
- Final verified revision: the `main` tip containing this report (named in
  the #114 closure comment; the scenario output records the exact hash of
  the tree it ran against).

## Assembled synthetic installation scenario

`docs/reviews/2026-09-12-issue-114-stage-gate-scenario.py` builds one
synthetic local installation and drives it through the batch contracts in a
single run. Command:

```text
PYTHONPATH=src python docs/reviews/2026-09-12-issue-114-stage-gate-scenario.py
```

Result on the final revision: **16/16 phases PASS**, exit code 0:

| Phase | Evidence |
| --- | --- |
| setup | `setup_instance` + `load_installation_runtime` produce a usable local installation |
| model receipts | two independent web/Gateway-role applications each persist their own run receipt (2 distinct receipts) |
| channel sessions | web and Telegram sessions both survive independent writers (#117) |
| processing denied by default | a cloud destination without a grant blocks the required category |
| processing grant conflict | a stale grant write is rejected with `ProcessingGrantConflict` |
| personal context | correction advances to version 2, deletion leaves a suppression marker, a stale correction is rejected (#106) |
| continuity conflict | stale confirmation rejected with `ReckoningRevisionConflict`; both instances' check-ins recorded (#107) |
| telegram first pass | provider outage marks the update `provider-failed`; the later update still delivers (#111) |
| telegram recovery | a restarted worker reprocesses only the failed update and delivers its reply; completed work is not repeated |
| trial gate | acceptance without complete evidence is refused; the report stays `pending` (#112) |
| trial acceptance | explicit decision recorded after complete evidence; report shows accepted for the tested revision |
| restart | decisions, check-ins, receipts, suppression marker, and Telegram records all visible after restart |
| restore identifiers/versions | decision id and version, corrected context meaning, trial decision, and Telegram update ids intact after encrypted clean restore |
| restore suppression markers | the deleted record's suppression marker survives the restore |
| restore permissions | every restored file is mode 0600 |
| restore usability | the restored installation accepts new work through its public boundary (third receipt persists) |

## #113 reconciliation

`docs/reviews/2026-09-11-issue-113-shared-state-probes.py` now derives its
result labels from observations (previously hardcoded at audit time). Rerun on
the final revision:

```text
interfaces:        preserved  (web:web-session and telegram:telegram-session both survive)
automation:        preserved  (routine-a and routine-b both confirmed)
external writes:   preserved  (both prepared writes survive; no pending after
                   two approvals; revoked_permission_restored = false)
processing grants: preserved-with-conflict (as at audit time)
```

All three critical findings from #113 are fixed by the merged #117/#118/#119
work and verified together here. No remaining critical shared-state, deletion,
permission, or execution failure is known; no new follow-up blocker is
required.

## Setup revalidation

- Essential setup path: the scenario's phase 0 plus
  `tests/test_setup_cli.py::test_setup_on_an_unconfigured_installation_starts_onboarding`
  and the guided-completion workflow tests.
- Demo-versus-real readiness: the status view reports `fake (demo mode)`
  (`tests/test_setup_cli.py` line 68 assertion) and workflow tests prove
  `outcome.demo` separates demo from verified-real activations
  (`tests/test_setup_workflow.py`, demo assertions at lines 176/322/333 and
  non-demo at 622/626).
- Draft/activation separation: interrupted setup exits with an incomplete
  status and a resumable draft (`test_setup_cli.py`), and activation writes
  last during migration (commit-phase interruption tests).
- #103 bounded controlled-terminal regression:
  `tests/test_setup_cli.py::test_interactive_setup_prints_each_step_before_waiting_for_input`
  plus the two pty renderer tests in `tests/test_setup_terminal.py` — every
  read waits on a 2-second deadline and the file completes in under a second.
  Bounded run: `timeout 60 python -m pytest -q tests/test_setup_terminal.py -k
  "pty or arrow"` → 4 passed in 0.33s. No new prompt/arrow-menu ticket was
  created.
- Installed provider/placement behavior: provider registry contract tests pin
  that only evidence-backed providers are selectable (`openai` and `custom`
  stay coming-soon; see #94's closure comment).

## Required checks on the final revision

```text
PYTHONPATH=src python -m pytest -q                       → 628 passed, 15 skipped
python -m mypy --python-version 3.13 --ignore-missing-imports src
                                                         → Success: no issues (42 files)
python -m ruff check --select E4,E7,E9,F src tests       → All checks passed
git diff --check                                         → clean
```

Skips: 15, all opt-in or platform gates — live smoke tests
(`RECKONING_LIVE_SMOKE=1` not set; no credentials or paid-call authorization
in this environment, per the product owner's decision in #94) and
posix-only pty tests when not on a posix terminal. No failures, no timeouts.

Local untracked helper scripts (not part of the tracked tree):
`scripts/check_grilling_questionnaire.sh` passes;
`scripts/check_product_spec.sh` fails its story-count assertion (147 vs an
expected 100) against the untracked local spec mirror — a local planning
artifact, recorded here for completeness, not a gate on this batch.

Live external gate: none run. The only attempted live call was a DeepSeek
setup verification authorized by the product owner during #101 evidence
gathering; it failed with the provider's own 401 (key on record invalid), was
normalized and redacted correctly, and activated nothing.

## Scope guardrails honored

#80 and the provider catalog keep their scope: #102 stays open, no provider
beyond the existing evidence-backed set was enabled, and nothing here claims
public-product acceptance. The parent product specification (#1) was not
changed.

## Self-review

- The scenario asserts through public boundaries only and stores no real data.
- The merge resolution keeps every schema validator; the full suite, mypy, and
  ruff pass on the merged tree.
- The probe label change makes reruns truthful instead of echoing the audit.
- Residual risk: pty evidence is Linux-only (see #101's closure comment);
  macOS/WSL share the same posix code path but were not run here.

## Next-stage handoff

The repaired foundation is verified: durable receipts, personal-context
authority, continuity conflicts, Telegram recovery, interface/routine/
external-write authority, trial gating, setup, and encrypted restore all hold
together on one installation.

The next stage must implement the complete ordinary web/Telegram continuity
journey from proposal through outcome (per the parent contract): a user sends
an ordinary message on web or Telegram, the agent proposes and the user
decides, the decision's reasons persist, execution follows the granted
authority, and the outcome check-in revisits the decision — across restarts
and both channels. Repaired service operations alone do not deliver that
journey; build it as its own bounded tickets from the full product contract.
