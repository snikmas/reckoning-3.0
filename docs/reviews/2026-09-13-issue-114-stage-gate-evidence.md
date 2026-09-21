# Issue #114 stage-gate evidence

Verification date: 2026-09-21.

This report restores the missing #114 gate on current history and records a
fresh non-live run. It uses synthetic data and fake transports. It does not
use credentials, paid providers, private data, or real Telegram delivery.

## Revision and working tree

The implementation revision under test was
`70cf1a7beb55ba93369d320c571e5cb089404e7f` on
`codex/audit-followups-152-155`. It contains these repair commits:

- `8a5b7f8` keeps connector revocation authoritative across instances.
- `9f5e65f` records actual delegated model cost after a quote breach.
- `70cf1a7` restores and modernizes the 16-phase #114 scenario.

The scenario reported a dirty working tree. The only dirty paths were this
untracked evidence report and two preserved audit artifacts:

- `docs/reviews/2026-09-21-phase-1-repair-plan/`
- `docs/reviews/2026-09-21-phase-2-post-repair-canonical-review.md`

The two audit artifacts were not staged, edited, or absorbed into the repair.
The commit that adds this report is recorded in the #153 delivery comment
because a commit cannot contain its own hash.

## Direct 16-phase scenario

Command:

```text
PYTHONPATH=src .venv/bin/python docs/reviews/2026-09-12-issue-114-stage-gate-scenario.py
```

Result: 16 passed, 0 failed. The command exited 0.

| Phase | Result | Evidence |
| --- | --- | --- |
| setup | Passed | The synthetic local installation loaded through the public runtime. |
| model receipts | Passed | Two independent applications stored two distinct receipts. |
| channel sessions | Passed | Web and Telegram sessions survived independent writers. |
| processing denied by default | Passed | A cloud destination without a grant blocked the required category. |
| processing grant conflict | Passed | A stale grant write raised `ProcessingGrantConflict`. |
| personal context | Passed | Correction reached version 2, deletion left a suppression marker, and stale correction failed. |
| continuity conflict | Passed | Stale confirmation failed and both instances kept their check-ins. |
| telegram first pass | Passed | A provider failure remained recorded while the later update delivered. |
| telegram recovery | Passed | Restart retried only the failed update and did not repeat completed work. |
| trial gate | Passed | Incomplete evidence kept the trial pending. |
| trial acceptance | Passed | Complete evidence plus an explicit decision accepted the tested revision. |
| restart | Passed | Decisions, check-ins, receipts, suppression, and Telegram records survived restart. |
| restore identifiers and versions | Passed | Identifiers, versions, the trial decision, and Telegram update IDs survived restore. |
| restore suppression markers | Passed | The deleted record's suppression marker survived restore. |
| restore permissions | Passed | Every restored file had mode `0600`. |
| restore usability | Passed | The restored installation accepted a new run through its public boundary. |

The responder now returns `ChannelResponse`. The fake Telegram client also
implements the current `TelegramBotClient` protocol. The scenario runs from
the tracked repository path without a temporary source rewrite.

## Passed checks

| Check | Result |
| --- | --- |
| Focused connector, recovery, interface, and delegation tests | 96 passed |
| Full suite with `RECKONING_LIVE_SMOKE=0` | 862 passed, 15 skipped |
| Mypy on `src` | Passed on 48 source files |
| Mypy on the restored scenario | Passed on 1 file |
| Ruff `E4,E7,E9,F` on `src`, `tests`, and the scenario | Passed |
| Python compilation on `src`, `tests`, and the scenario | Passed |
| `git diff --check` | Passed |
| Controlled-terminal `pty or arrow` subset | 5 passed, 29 deselected |

The full suite had 0 failures and 0 excluded test files. The 15 skipped tests
remain skipped evidence, not passes.

## Failed repository checks

`scripts/check_product_spec.sh` and
`scripts/check_grilling_questionnaire.sh` both exited 1 before checking
content. Their required inputs are absent from current `main`:

- `docs/product-spec.md`
- `docs/product-grilling-questionnaire.md`
- `docs/product-grilling-decisions.md`

This report does not convert those failures into passes. The scripts and their
missing inputs are outside the three code defects in #152 through #154.

## Skipped, deselected, and unrun work

- 15 tests skipped under the repository's non-live configuration.
- 29 setup-terminal tests were deselected by the focused `pty or arrow` filter.
- Live paid-provider setup and conversation were not run.
- Live OpenAI and a real custom endpoint were not run or enabled.
- Live Telegram delivery was not run.
- Real-browser manual checks were not rerun for this restoration.
- macOS and WSL terminal checks were not run.
- No credential, secret, or private profile file was read.

OpenAI remains unavailable until an authorized installed journey covers
setup, key verification, activation, conversation, receipt and status truth,
restart, and repair. A custom OpenAI-compatible endpoint remains unavailable
until that configured endpoint passes its own verification and real endpoint
test.

## Product and evidence classification

#50, #51, #52, and #60 are service foundations. Their direct-service tests do
not prove supported CLI, web, Telegram, or scheduler journeys. #61 and #62 are
historical evidence records. #123 is a simulated prototype, not production
Stage 2 evidence.

The connector and delegation defects are repaired at the service level. That
does not promote either service into a completed user-facing capability.
