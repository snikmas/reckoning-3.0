# Setup, credentials, and migrations review

Review date: 2026-09-22

Baseline: local `main` and live `origin/main` both resolved to `4487ae3` when
the review started. GitHub reported 125 closed issues and 21 open issues. The
working tree already contained the preserved Phase 1 plan, Phase 2 report, and
this review TODO as untracked files.

## Scope and method

This review covers #64-#65, #68-#70, #73-#75, #80-#90, #92, #115, and #138.
Each issue was checked separately against its body, closure history, current
ancestry, current code, and focused tests. A later design may supersede an old
interaction without making the old literal interface current.

The focused command passed with `346 passed, 8 skipped`. It covered provider
configuration and redaction, provider contracts and availability, key
verification, processing grants, setup CLI and terminal behavior, setup
transactions, cloud continuity, cloud provider execution, Telegram pairing,
and recovery. A new non-interactive fake-provider installation also completed
from an empty temporary directory and reopened with its accepted first
conversation.

The eight skips are opt-in external-provider checks. No credential was read and
no live or paid request was made.

## Standards review

No hard repository-standard violation was found in this subsystem. The current
implementation keeps credentials out of instance state, writes owner-only
files, redacts provider failures, uses explicit state and revision checks, and
keeps setup mutations behind transactional snapshot and rollback code. Mypy,
Ruff, compilation, and the full suite are recorded in the final coverage
report rather than repeated here.

Two evidence defects remain in GitHub history. #64 has no closure comment, and
#138 has no final implementation comment. #115 records deterministic delivery
but correctly leaves its real DeepSeek check unrun. These are issue-history
defects, not code defects.

## Spec review

| Issue | Current-history evidence | Standards | Spec verdict | Current disposition and unrun gates |
| --- | --- | --- | --- | --- |
| #64 | Patch-equivalent `667daea` is on `origin/main`; Telegram setup, pairing, polling, offsets, authorization, and splitting tests pass | Pass | Superseded in interface, current in behavior | `reckoning gateway` and guided setup replaced `reckoning-telegram`; live Telegram pairing is unrun |
| #65 | Patch-equivalent `b5e9890` plus current setup flow are on `origin/main` | Pass | Pass under the current activation contract | Hidden entry, redaction, owner-only storage, environment override, rollback, and inactive-until-verified behavior pass; live providers unrun |
| #68 | `dfed1e0` is on `origin/main` | Pass | Pass | Multi-provider round trip, atomic `0600` writes, legacy loading, save-time migration, and environment override pass |
| #69 | `28c0faa` is on `origin/main` | Pass | Superseded | The old add-another loop and final default menu were replaced by #80/#89; durable default resolution remains tested |
| #70 | `f4c9a4c` is on `origin/main` | Pass | Superseded with surviving recovery guarantees | Retry/edit/back/save-for-later/exit replaced the old three-choice screen; abort and failed activation leave no partial active state |
| #73 | Patch-equivalent `6067cfc` is on `origin/main` | Pass | Pass | Unified dispatcher, grouped help, data commands, and removed `reckoning-ops` entry point remain current |
| #74 | Patch-equivalent `6067cfc` is on `origin/main` | Pass | Pass | `reckoning gateway` remains the Telegram channel runner; real Telegram is unrun |
| #75 | Patch-equivalent `6067cfc` is on `origin/main` | Pass | Superseded | #80 replaced the old fixed step order and management menu; provider, interface, style, profile, review, and first-conversation outcomes survive |
| #80 | `947b388` and its current-history prerequisites are on `origin/main` | Pass | Pass on deterministic evidence | Guided setup, current provider truth, Signal rendering, accepted first conversation, installed status, and recovery pass; paid providers, macOS, and WSL are unrun here |
| #81 | Patch-equivalent `947b388` is on `origin/main` | Pass | Pass | Setup decisions remain separate from terminal rendering through the scripted UI and Signal renderer seams |
| #82 | Patch-equivalent `947b388` is on `origin/main` | Pass | Superseded | Quick Setup was deliberately removed by #80; Demo labeling, retry, acceptance, temporary rejected attempts, durable accepted history, and reconstruction still pass |
| #83 | `065481f` is on `origin/main` | Pass | Pass | Resume, activation, accepted-exchange durability, installed-state detection, and fresh application reopen pass |
| #84 | Patch-equivalent `947b388` plus registry hardening `7b0407c` are on `origin/main` | Pass | Pass | Available providers use the versioned registry; unsupported providers have no adapter and remain hidden or coming soon |
| #85 | Patch-equivalent `947b388` is on `origin/main` | Pass | Superseded | Quick/Custom modes were removed; explicit local, personal-server, and hybrid placement remains in the one guided flow |
| #86 | Patch-equivalent `947b388` is on `origin/main` | Pass | Pass under #80 wording | Simon, Steady, and authored style preview remain supported without restoring the old mode structure |
| #87 | Patch-equivalent `947b388` and profile persistence repairs are on `origin/main` | Pass | Pass | Guided/import/skip profile paths, reviewable proposals, raw-file non-retention, and private placement pass |
| #88 | Patch-equivalent `947b388` is on `origin/main` | Pass | Pass on deterministic evidence | Bot verification, private-chat pairing, stored authorization, skip, and edit paths pass; live Telegram is unrun |
| #89 | `065481f` is on `origin/main` | Pass | Pass | Status, focused editing, invalid-draft repair, installed provider preservation, and restart behavior pass |
| #90 | `7b0407c` is on `origin/main` | Pass | Pass | Preview and cancellation do not mutate state; backup and commit are atomic; interruptions restore or leave a reopenable installation; supported state and grants survive |
| #92 | `7b0407c` is on `origin/main` | Pass | Pass | Reset previews exact normalized targets, preserves credentials by default, requires explicit risk acceptance, rejects broad paths, and leaves cancelled runs unchanged |
| #115 | `c4154f2` merged by `f2aeb75`, both on `origin/main` | Pass | Pass on deterministic evidence | DeepSeek verification disables thinking only for the probe, rejects null and truncated output safely, redacts the key, and keeps normal completion unchanged; real DeepSeek verification is unrun |
| #138 | `cc5ee00` and current-history equivalent `f16c33f` are on `origin/main` | Pass | Pass | Cloud activation and provider change create the full-category grant transactionally; rollback removes it; narrowed grants stay narrowed; fake/local providers create none; migration and doctor repair pass |

## Direct risk results

- Credential replacement, reset, redaction, and `0600` persistence passed.
- Verification and activation rollback passed. An activation failure restores
  the prior provider and grant store.
- A verified cloud destination receives its initial grant in the same
  activation transaction. Later narrowing or revocation remains authoritative.
- Migration preview, cancellation, and skip leave supported files unchanged.
  Interrupted commit and backup failure probes preserve a reopenable original.
- Normal doctor remains diagnostic. `doctor --repair` is the explicit missing
  grant repair path and reports the destination and categories it writes.
- Installed-state reopen, non-interactive parity, Telegram authorization,
  unsupported-provider gating, stale revision checks, and clean install all
  passed through current tests.

## Disposition

The subsystem passes on current deterministic evidence. Historical UI wording
for #64, #69, #70, #75, #82, and #85 is superseded rather than literally
restored. The final GitHub update must add current delivery evidence to #64 and
#138 and retain the explicit live limits on #115 and Telegram.
