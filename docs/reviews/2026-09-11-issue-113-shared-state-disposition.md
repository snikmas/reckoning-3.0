# Issue 113 shared-state disposition

## Result

The audit found three confirmed shared-state failures in active public boundaries:

- `interfaces.json` loses one channel session when two application instances save
  snapshots that came from the same earlier state.
- `automation.json` lets one stale confirmation restore another confirmed routine
  to proposed.
- `external-writes.json` loses prepared writes and exact approvals. A stale writer
  can also restore a revoked standing permission.

These failures block the Stage 1 integration gate in issue #114. The current
installation cannot yet claim that channel history, routine authority, or
external-write authority remains correct across independent web and Gateway
processes. Three bounded follow-up issues now own the fixes:

- [#117 Preserve channel state across independent applications](https://github.com/snikmas/reckoning-3.0/issues/117)
- [#118 Preserve routine authority and execution records across workers](https://github.com/snikmas/reckoning-3.0/issues/118)
- [#119 Keep external-write revocation authoritative](https://github.com/snikmas/reckoning-3.0/issues/119)

All three are open, `bug`, `ready-for-agent`, and already registered as blockers
of #114. Closing this diagnostic does not fix any of the three failures.

The processing-grant control preserved writes for two destinations and rejected a
stale revision for the same destination. The remaining JSON stores have no
supported concurrent runtime writers today. Their current ownership limits the
immediate risk, but their snapshot implementations are not safe to activate from
several processes.

## Scope and tested state

The audit used synthetic state only. It did not read credentials, private user
state, or live connector data. It made no provider, Telegram, or external-service
request.

- Repository: `snikmas/reckoning-3.0`
- Baseline revision: `dbe40e3defb2e27012b8cff226450a35f2784d16`
- Baseline branch: `main`, equal to `origin/main` at the start of this audit
- Working state: clean after issue #111 landed as commit
  `dbe40e3defb2e27012b8cff226450a35f2784d16`; only this report and its probe
  are uncommitted during the rerun
- Probe: `docs/reviews/2026-09-11-issue-113-shared-state-probes.py`
- Issue #111 commit: `dbe40e3defb2e27012b8cff226450a35f2784d16`
- New Telegram repository Git blob ID:
  `7f13116598d81b813a4d9d9efd0207834c53c63e`
- Shared-state probe Git blob ID:
  `e586271c41fd2779776724d82454a7c3abab1b7b`

The issue #111 commit identifies the durable Telegram implementation that this
rerun measured. The probe blob identifies the exact reproduction source
independently of this report.

Run the reproduction with:

```text
PYTHONPATH=src python docs/reviews/2026-09-11-issue-113-shared-state-probes.py
```

The rerun against `dbe40e3` returned these results:

```text
interfaces:        expected [web:web-session, telegram:telegram-session];
                   surviving [telegram:telegram-session]
automation:        expected both confirmed; observed routine-a proposed,
                   routine-b confirmed
external writes:   expected [write-a, write-b]; surviving [write-b]
external writes:   expected [] pending after two approvals; observed [write-a]
external writes:   revoked_permission_restored = true
processing grants: preserved provider-a and provider-b; stale same-destination
                   write rejected
```

`atomic_write_json()` prevents malformed partial files. It does not prevent lost
updates. Each unsafe repository loads the whole file into memory, changes its own
copy, and replaces the whole file without a lock or expected revision.

## Store inventory

| State | Placement and callers | Concurrent-access evidence | Disposition |
| --- | --- | --- | --- |
| Model-run receipts | Placement-root `reckoning.sqlite3`; web and Gateway model calls | Issue #105 moved the store to per-record SQLite transactions and covered independent instances and recovery. | Known repair. Do not duplicate it here. |
| Personal context | Placement-root `reckoning.sqlite3`; web and Gateway conversation and context operations | Issue #106 added transactional versions and deletion authority. | Known repair. Do not duplicate it here. |
| Continuity decisions and check-ins | Placement-root `reckoning.sqlite3`; application decision and outcome operations | Issue #107 added current reads, expected versions, and per-record transactions. | Known repair. Do not duplicate it here. |
| Telegram inbox and outbox | Placement-root `reckoning.sqlite3`; Gateway polling | Issue #111 stores accepted updates, completed replies, delivery outcomes, and the polling offset in transactions. | Known repair. Committed as #111 (`dbe40e3`). |
| Trial data | Placement-root `reckoning.sqlite3`; release-readiness inspection and trial recording tools | `JsonFileTrialRepository` is a compatibility entry point backed by transactions and expected versions. Issue #112 covers explicit acceptance. | No remaining shared-state failure found. |
| Interface sessions and projections | `confirmed-state/interfaces.json`; both `reckoning web` and `reckoning gateway` create `JsonFileInterfaceRepository` instances | The probe saved independent web and Telegram sessions from two stale snapshots. Only the second session remained. The same whole-state replacement covers receipts, failures, permissions, approvals, and activity. | Confirmed critical gap. Draft A is required before #114 can pass. |
| Processing grants | `confirmed-state/processing-grants.json`; web changes grants and each model call reads the current grant | `_grant_write_lock()` serializes writers. `save()` reloads inside the lock and checks the expected destination revision. The control probe preserved both destinations and rejected a stale same-destination write. | Current design is safe for the tested operations. |
| Routine proposals, runs, and receipts | `confirmed-state/automation.json`; web and Gateway confirmations use `LocalDurableConfirmationHandler`; scheduled execution has no installed runtime entry point | Two stale `RoutineService` instances confirmed different seeded routines. The second whole-file flush restored the first routine from confirmed to proposed. Competing scheduled workers would use the same unsafe repository when that runtime is installed. | Confirmed authority gap. Draft B is required before #114 can pass. |
| Prepared external writes, approvals, permissions, and receipts | `confirmed-state/external-writes.json`; web and Gateway confirmations use `LocalDurableConfirmationHandler` | The probe lost one prepared write and one of two exact approvals. A stale instance also restored a revoked standing permission, and a later synthetic write executed under that restored permission. | Confirmed privacy and authority gap. Draft C is required before #114 can pass. |
| Imported connector state | `approved-remote-sources/connectors.json`; Control reads it through `LocalOperationalRecordSource` | Production code does not construct `ConnectorService` from the web, Gateway, or command entry point. The installed runtime is read-only for this file. | Not a current concurrency blocker. Require transactional storage before a connector writer is installed. |
| Watches | `confirmed-state/watches.json`; Control reads receipts and health | Production code defines `WatchService`, but no installed entry point constructs it. The installed runtime is read-only for this file. | Not a current concurrency blocker. Fix its snapshot store when scheduled watch execution is wired. |
| Briefings | `confirmed-state/briefings.json`; Control reads receipts | Production code defines `BriefingService`, but no installed entry point constructs it. The installed runtime is read-only for this file. | Not a current concurrency blocker. Fix its snapshot store when briefing delivery is wired. |
| Delegation receipts | Caller-selected JSON path in `JsonFileDelegationReceiptRepository` | No installed web, Gateway, command, or scheduler composition constructs the durable repository. Current delegation tests inject it directly. | Not a current concurrency blocker. Add transactional receipt storage before runtime delegation is installed. |
| Personas | Local-root `personas.json`; setup and `reckoning persona` write it; web and Gateway read the selected persona during startup | Running web and Gateway processes do not write persona state. Supported mutation is an operator configuration action. | Current ownership is single-writer in normal operation. Issues #80 and #91 own setup and focused-command work. Do not fold it into Stage 1. |
| Provider credentials | Private configuration file outside the placement roots; setup and provider commands write it; runtimes read it during startup | The audit inspected callers and field names only. It did not read the credential file. Running web and Gateway processes do not write it. | Current ownership is setup-only. Provider tickets #94 through #100 own later catalog work. |
| Telegram connector configuration | Private configuration file outside the placement roots; setup writes it and Gateway reads it during startup | Running Gateway code does not rewrite the file or its legacy offset. Issue #111 adopts the configured offset only when the durable inbox has no cursor. | Current ownership is setup-only. No unseen update is marked delivered during adoption. |
| Installation and release evidence | Local-root `instance.json` and `release-evidence.json`; setup writes them and runtime or release checks read them | The supported web, Gateway, and scheduled paths do not mutate either file. Restore rewrites only the staged `instance.json` before the atomic root swap. | Current ownership is setup or restore only. |
| Planning and forecasts | In-memory repositories only | `InMemoryPlanningRepository` and `InMemoryResearchForecastRepository` have no durable implementation and no installed web, Gateway, or scheduler composition. | These are later capability gaps, not remaining durable-store races. Do not treat tests as an installed feature. |

## Effects on product gates

The interface failure can erase a completed reply or another channel's history.
That breaks the core continuity loop and truthful operational inspection.

The automation failure can restore a confirmed routine contract to proposed. The
same replacement path can erase a run or receipt. This breaks durable execution
and makes restart behavior depend on which process wrote last.

The external-write failure is the most serious result. A revoked permission can
become active again after an unrelated stale write. This breaks user authority,
permission revocation, and the external-action safety gate.

Issue #113 closes as a completed diagnostic after these findings and published
follow-up issues. Issue #114 must remain blocked until the critical fixes are
implemented and verified together.

## Draft A: Preserve channel state across independent applications (#117)

### Outcome

Independent web and Gateway applications preserve both channel sessions and
operational projections. A stale replacement cannot erase another process's
completed state.

### Acceptance checks

- Store session turns and mutable interface projections in per-placement-root
  transactions. Do not keep one cached whole-file snapshot as write authority.
- Append a completed user and assistant turn without replacing another session.
  Preserve current activity, receipts, failures, permissions, and approvals with
  explicit update rules.
- Use current reads and expected revisions for state that replaces an earlier
  value. Return a conflict instead of silently overwriting a newer value.
- Migrate valid `interfaces.json` state once without losing channel identity,
  order, or operational records. Keep interrupted migration recoverable.
- Cover independent processes, concurrent first initialization, two channels,
  restart, and encrypted clean restore with synthetic data.
- Keep the change within interface persistence. Do not redesign the Simon or Home
  presentation.

### Genuine blockers

None. Published as #117 and registered as a blocker of #114.

## Draft B: Preserve routine authority and execution records across workers (#118)

### Outcome

Routine proposals, confirmations, runs, and receipts remain authoritative when
independent interfaces or scheduled workers use the same installation.

### Acceptance checks

- Replace whole-file automation snapshots with per-placement-root transactions.
- Preserve independent routine proposals and confirmations. A stale process
  cannot restore a superseded contract or erase a confirmed contract.
- Claim one scheduled run atomically by its idempotency key. Competing workers
  cannot execute the same active run without an explicit recovery transition.
- Persist step progress and the terminal receipt with current reads. Restart
  resumes bounded work without discarding completed steps.
- Migrate valid `automation.json` data once. Cover concurrent confirmations,
  competing run claims, restart, and encrypted clean restore.
- Keep watches and briefings out of this ticket because no installed writer uses
  them yet.

### Genuine blockers

None. Published as #118 and registered as a blocker of #114.

## Draft C: Keep external-write revocation authoritative (#119)

### Outcome

Prepared external writes, exact approvals, standing permissions, revocations, and
receipts cannot be lost or restored by a stale process.

### Acceptance checks

- Store external-write authority and receipts in per-placement-root transactions.
- Make a standing-permission revocation monotonic. No stale write can make the
  permission active again.
- Preserve independent prepared writes and exact approvals across web and Gateway
  confirmations.
- Claim execution atomically by the prepared-write identifier and payload digest.
  Reuse the idempotency key and recorded receipt after restart.
- Record an explicit unknown outcome when the adapter result is ambiguous. Do not
  claim that an external effect did or did not occur without evidence.
- Migrate valid `external-writes.json` state once. Cover concurrent confirmation,
  revocation versus stale write, competing execution, restart, and encrypted clean
  restore with a fake connector.

### Genuine blockers

None. Published as #119 and registered as a blocker of #114.

## Verification commands

```text
PYTHONPATH=src python docs/reviews/2026-09-11-issue-113-shared-state-probes.py
PYTHONPATH=src python -m pytest -q tests/test_telegram_runtime.py
PYTHONPATH=src python -m pytest -q
git diff --check
```

Observed results on `dbe40e3`:

- Shared-state probe: one lost session, one reverted routine confirmation, one lost
  prepared write, one lost exact approval, one restored revoked permission, and one
  safe processing-grant control.
- Telegram regression file: `17 passed`.
- Full suite: `522 passed, 15 skipped in 3.86s`.
- `git diff --check`: passed.

The three confirmed failures are reproduction evidence, not failing tests to fix
inside this diagnostic. They are owned by the published follow-up issues:

- #117 covers the lost channel session and interface projections.
- #118 covers the reverted routine confirmation and routine authority.
- #119 covers the lost prepared write, lost exact approval, and restored revoked
  permission.

All three already block #114. Issue #113 closes as a completed diagnostic only.
It does not fix the three failures, and #114 must remain blocked until #117, #118,
and #119 are implemented and verified together.
