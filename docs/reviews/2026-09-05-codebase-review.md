# Reckoning codebase review and possible solutions

Review date: 2026-09-05. Reviewer: Astra. Prepared for independent review by Sol.

## Assessment

The product idea is more coherent than the connected implementation. Reckoning
has useful foundations: proposed versus confirmed context, preserved corrections,
explicit permission contracts, injectable dependencies, and recovery operations.
However, several guarantees fail when components interact.

The strongest product direction is the core continuity loop: preserve why a
decision was made, revisit it when circumstances change, and use its outcome to
improve later judgment. The current priority should be a reliable, usable version
of that loop. Further provider coverage can wait.

This document records findings and candidate remedies. It is not an approved
implementation specification. Sol should independently reproduce the findings,
challenge their interpretation, and assess the proposed solutions before code
changes are planned.

## Scope and evidence

The reviewed checkout is `/home/snikmas/work/projects/reckoning-3`, with remote
`https://github.com/snikmas/reckoning-3.0.git`.

HEAD at review time was `7c93c89dee5d6ce38721542dece89f15db788fa8`.
The review included uncommitted changes in 13 files. HEAD alone therefore does
not identify the exact reviewed code. Those files were:

- `src/reckoning/application.py`
- `src/reckoning/command.py`
- `src/reckoning/config.py`
- `src/reckoning/provider_adapters.py`
- `src/reckoning/providers.py`
- `src/reckoning/setup_terminal.py`
- `src/reckoning/setup_workflow.py`
- `src/reckoning/telegram.py`
- `src/reckoning/web.py`
- `tests/test_provider_config.py`
- `tests/test_setup_cli.py`
- `tests/test_setup_terminal.py`
- `tests/test_setup_workflow.py`

The review examined the application boundary, persistence, personal context,
placement, provider adapters, setup, interfaces, Telegram, permissions, routines,
planning, delegation, recovery, and trial acceptance. This was a broad review,
not an exhaustive security audit or a live-provider behavior evaluation.

Evidence from the review:

- The full test suite passed with **381 passed, 11 skipped** in an isolated
  environment. This count is a snapshot, not a claim about later changes.
- Additional synthetic probes reproduced the failures described below.
- No real credentials, private profiles, or live application data were read.
- No live model or Telegram requests were made by the probes.
- The review did not modify production code or existing tests.

The first isolated test run failed because one test reads `pyproject.toml` from
the current directory. Copying that public file into the temporary working
directory resolved the test-environment issue. It was not counted as a product bug.

The companion [reproduction script](2026-09-05-review-probes.py) uses synthetic
records, temporary directories, and fake transports. Its assertions intentionally
check for the observed bugs. A failed assertion after a fix can mean the bug is
gone; these probes are not a replacement for regression tests of correct behavior.

## 1. Shared instances can lose records and resurrect deleted memory

Priority: high. Status: reproduced with two independently constructed instances
in one process. Separate OS processes were not needed to trigger the stale state.

### Evidence and cause

Several JSON repositories load state once in their constructors. Later writes
replace the entire file with that instance's in-memory snapshot.

The probes demonstrated two failures:

- Two application instances each completed a model call, but only one model-run
  receipt remained on disk.
- One personal-context service deleted a record. A second service still retrieved
  that record. When the second service saved an unrelated record, it restored the
  deleted content and removed the deletion marker from the stored snapshot.

Web and Telegram both construct application instances that can use the same
installation files. This makes the stale-snapshot problem relevant to normal use.

Atomic file replacement prevents a partial JSON document. It does not prevent
lost updates or enforce deletion across readers.

Relevant code:

- [Personal-context repository](../../src/reckoning/personal_context.py),
  `JsonFilePersonalContextRepository.__init__`, `save`, `delete`, and `_flush`.
- [Model-run repository](../../src/reckoning/application.py),
  `JsonFileModelRunRepository.save_run`.
- [Continuity repository](../../src/reckoning/persistence.py),
  `JsonFileReckoningRepository` uses the same broad storage pattern.

### Possible solutions

The preferred direction is per-record SQLite operations inside transactions,
with current reads at operation boundaries. Avoid storing the same stale whole
snapshot in one database row, which would preserve the underlying defect.

Preserve placement boundaries during any migration. One database containing all
categories must not accidentally copy private categories onto a server. Each
storage root may need its own database or a process that owns that root.

A smaller interim solution is an interprocess lock around each complete
read-modify-write operation, followed by a reload while holding the lock.
Reads that must respect deletion also need current state. Locking only the file
replacement is insufficient.

A single application process owning state and serving both channel adapters is
another option. It simplifies ownership but changes how web and Gateway run.

### Verification required

- Two instances append different receipts without losing either receipt.
- A deletion in one instance immediately excludes the record from another
  instance's next retrieval.
- An unrelated write cannot restore deleted content or erase a deletion marker.
- Concurrent corrections preserve version order or return an explicit conflict.
- Process interruption preserves recoverable state.
- Migration and backup preserve identifiers, versions, placement, and deletion.

SQLite's [isolation documentation](https://www.sqlite.org/isolation.html) explains
cross-connection transactions. Database isolation does not remove the need for
correct application transaction boundaries.

## 2. Local processing does not enforce the model destination

Priority: high. Status: reproduced by inspecting an outgoing request through a
fake cloud-provider transport.

### Evidence and cause

`load_installation_runtime` derives `processing_location` from the node that
stores confirmed state. `_personal_context_for_message` uses that location to
select records. The provider adapter then sends the assembled prompt to the
selected model endpoint.

A synthetic private record marked `processing_location="local"` appeared in
a DeepSeek request. No network request was sent.

Selecting a cloud provider can authorize cloud conversation. The narrower defect
is that the code does not independently enforce which stored context may
accompany it. Storage location, application location, and model-processing
permission are conflated.

Relevant code:

- [Runtime placement](../../src/reckoning/operations.py),
  `load_installation_runtime`.
- [Context selection](../../src/reckoning/application.py),
  `_personal_context_for_message`.
- [Provider request construction](../../src/reckoning/provider_adapters.py),
  `RuntimeAdapterModelProvider.respond`.

### Possible solution

Represent the actual model destination separately from storage placement.
Evaluate category-level processing grants before personal context enters a
request. Keep category-level controls consistent with the accepted placement ADR.

When required context cannot be sent, either omit it and declare limited mode or
block the operation if a partial answer would be misleading. Provider selection
must not silently broaden an existing category permission.

Apply the same policy to conversation history, summaries, structured reckoning,
and delegated work. Filtering only newly retrieved records leaves other routes
for restricted information to reach the provider.

### Verification required

- Local-only private context is absent from a cloud request.
- Explicitly approved categories can reach the intended destination.
- Changing the provider does not expand processing permission.
- History and derived summaries obey the same restriction.
- Limited mode describes missing context accurately.

Sol should confirm the intended semantics of existing placement settings before
proposing a migration or a new user-facing consent step.

## 3. The core continuity loop is disconnected from ordinary channel use

Priority: high for product usefulness. Status: source inspection plus a synthetic
web request. This is a completeness gap, not an established regression.

### Evidence and cause

The application exposes `start_reckoning`, correction, confirmation,
`resume_decision`, and `record_check_in`. Ordinary web and Telegram messages use
the conversation path instead. The review found no channel calls into those
structured decision operations.

A web message saved two conversation messages but created no structured
continuity record. Plan and Review render placeholder text.

A separate navigation bug hides the immediate response: successful message
submission redirects to `/`, and the interface marks the user as returning.
The destination becomes Home, so the user must return to Simon to read the reply.

Relevant code:

- [Web adapter](../../src/reckoning/web.py), `_handle_interface_request` and
  `_render_interface_area`.
- [Channel application](../../src/reckoning/interfaces.py),
  `send_channel_message` and `ApplicationChannelResponder`.
- [Continuity operations](../../src/reckoning/application.py),
  `start_reckoning`, `confirm_reckoning`, `resume_decision`, and `record_check_in`.

### Possible solution

First, redirect a successful conversation submission to `/simon` so the reply is
visible. Keep Home as the returning-user entry point for a fresh visit.

Then connect one complete continuity journey to the application boundary used by
the channels. The model may propose a decision, but deterministic application
operations must own validation, corrections, confirmation, identifiers, and
persistence. Do not turn every casual message into a confirmed personal record.

Home and Review should read the same durable decisions and check-ins. Avoid
manually maintained copies of confirmed state inside interface snapshots.

### Verification required

Use the actual channel boundary to complete this journey:

1. Describe a meaningful conflict.
2. Receive a proposed reckoning with reasons and uncertainty.
3. Correct the proposed meaning.
4. Confirm the decision.
5. Reconstruct the application from its stored state.
6. Resume the same decision and record its outcome.
7. Use that outcome as evidence in a later conversation.

The test must not bypass missing UI or channel behavior by calling domain
services directly halfway through the journey. Casual conversation also needs a
test proving that it does not silently confirm a decision.

## 4. Telegram delivery failures repeat work, and provider failures stop polling

Priority: high for runtime reliability. Status: reproduced with fake adapters and
a fake Telegram client.

### Evidence and cause

`run_once` returns its new offset only after processing and delivering the whole
batch. If delivery fails partway through, `run_forever` retains the old offset.
The retry can repeat work that already completed.

The probe processed updates in the order `1, 2, 1, 2`. It delivered replies in
the order `1, 1, 2`. With a live provider, repeating the processing could also
repeat paid model calls.

Separately, an application provider failure becomes `RuntimeError`.
`run_forever` catches `TelegramBotApiError`, so the provider error escapes and
terminates the loop.

Relevant code: [Telegram polling](../../src/reckoning/telegram.py),
`TelegramPollingApplication.run_once` and `run_forever`.

### Possible solution

Persist an inbox keyed by Telegram update ID and an outbox containing generated
replies. An inbox records accepted incoming work. An outbox records replies that
still need delivery. Once a response exists, retry delivery using that response
instead of asking the model again.

Catch expected provider failures at the per-update boundary, record their state,
and keep polling. Unexpected programming errors should remain visible rather
than being silently swallowed.

Define the recovery contract for an ambiguous send result. A network timeout
after the remote service accepts a reply cannot automatically guarantee exactly
one delivery. Distinguish that uncertainty from local processing deduplication.

### Verification required

- Failure on the second reply does not regenerate the first response.
- Redelivery of an update reuses its stored processing result.
- A provider outage is visible and does not terminate the Gateway.
- Restart between processing and delivery recovers the pending reply.
- Tests document the behavior after an ambiguous remote send result.

## 5. The web application accepts requests from foreign origins

Priority: high for mutation protection. Status: missing checks reproduced at the
WSGI application boundary. Browser exploitation was not tested.

### Evidence and cause

A POST with a foreign `Origin`, foreign `Host`, and
`Sec-Fetch-Site: cross-site` returned `303 See Other` and changed conversation
state. The application did not reject the request or require a CSRF token.

Loopback binding restricts exposure, but it does not itself distinguish a request
from the application's UI from a request induced by another site. Browser
protections and deployment details can affect exploitability, so the probe is
evidence of absent server checks rather than a complete browser attack.

Relevant code: [Web request handling](../../src/reckoning/web.py),
`ReckoningWebApplication.__call__`, `_handle_interface_request`, and form rendering.

### Possible solution

Validate the allowed Host values and protect all mutations with a coherent CSRF
policy. Options include a session-bound token with Origin validation, plus
Fetch Metadata checks as defense in depth. Keep intentional loopback and SSH
tunnel access usable. CORS alone is not a substitute for mutation protection.

Use the same protection for message creation and profile correction,
confirmation, and rejection. Do not limit checks to the Control page.

### Verification required

- Foreign-origin and foreign-host mutations are rejected before state changes.
- Missing and invalid CSRF tokens are rejected under the chosen policy.
- Legitimate forms work on supported local access paths.
- Every mutation route receives the same protection.
- A browser-level test checks the applicable cross-site request behavior.

Reference: [OWASP CSRF prevention guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).

## 6. Negative trial evidence still produces acceptance

Priority: medium. Status: reproduced. The role of this flag in release decisions
needs independent confirmation.

### Evidence and cause

`SliceTrial.accepted` returns true when every required evidence category is
present. It does not inspect whether the outcome is positive or whether the user
accepted the trial.

The probe records `All checks failed` for the automated check and negative text
for every real-use metric, including `not worth keeping`. The trial still reports
`accepted=True`.

This proves that the acceptance property is misleading. It does not establish
that this property alone controls a public release. The repository also contains
a separate `release_readiness` function.

Relevant code: [Trial acceptance](../../src/reckoning/trials.py),
`SliceTrial.missing_evidence` and `SliceTrial.accepted`.

### Possible solution

Separate evidence completeness from acceptance. Use structured check outcomes
and an explicit acceptance decision with its reason and date. Preserve negative
evidence instead of treating its presence as success.

Avoid parsing free-form text for positive or negative keywords. The user must
retain authority over the product judgment. Determine when later failures or
material changes require that acceptance to be reviewed again.

### Verification required

- Complete but negative evidence does not imply acceptance.
- A failed required check prevents acceptance.
- An explicit rejection persists across restart.
- Positive evidence without the required user decision remains unaccepted.
- Trial reporting and release-readiness reporting have consistent meanings.

## Product and architecture recommendations

### Connect existing capabilities before expanding coverage

The current tests are stronger at proving individual services than at proving
the assembled product. Add tests for real application construction, shared
storage, interruptions, and complete channel journeys. Keep deterministic
contract tests separate from live-model behavior evaluations.

Preserve the existing strengths: explicit versions, proposal boundaries,
injectable clocks and adapters, bounded permissions, and recovery checks. A
wholesale rewrite would discard useful work without resolving the integration
questions by itself.

### Make planning uncertainty visible

`GoalService.calculate_feasible_set` adjusts effort with
`weekly_effort_hours * (0.75 + difficulty * 0.1)` and rounds to two decimal places.
The review found no calibration evidence attached to that coefficient.

This is a design concern, not a demonstrated numerical correctness bug.
If the heuristic remains, label it as an assumption. Prefer ranges and visible
inputs until actual outcomes justify a more precise estimate. Evaluate whether
past check-ins improve future forecasts rather than only testing the arithmetic.

Relevant code: [Feasible-goal calculation](../../src/reckoning/planning.py),
`GoalService.calculate_feasible_set`.

### Suggested implementation sequence

1. Establish shared-state correctness and deletion behavior. Preserve category
   placement and verify migration and recovery.
2. Enforce model-processing permission and protect web mutations. These two
   fixes can be designed independently.
3. Correct the conversation redirect and connect the complete continuity
   journey through the shipped channel boundary.
4. Add durable Telegram processing and delivery recovery using the corrected
   persistence model.
5. Separate trial completeness from acceptance, then use the corrected trial
   model to evaluate repeated real use.
6. Reassess planning estimates and further capability expansion from that use.

The main product measure should be whether preserved reasons and outcomes help
the user make a later decision with less correction effort. Provider count and
passing test count do not establish that value.

## Independent review requested from Sol

The requested next step is to check this review, not automatically implement it.
For each finding, record whether it is confirmed, partly confirmed, refuted, or
no longer applicable to the current code. Include the supporting evidence and
the smallest appropriate remedy.

Particular questions to resolve:

- Which repositories and runtime processes actually share writable state?
- Does existing placement policy authorize cloud use of private context, or is
  new processing permission required?
- Are there channel routes added after this snapshot that complete the core loop?
- What Telegram delivery guarantee is achievable after an ambiguous send?
- Does `SliceTrial.accepted` affect release decisions or only reporting?
- Which proposed changes conflict with accepted ADRs or existing issue scope?

Preserve unrelated working-tree changes. Use temporary state and fake transports
for reproduction. Do not read real secrets or live personal data to validate
these findings.

## Reproduction commands

From the repository root, with the existing test environment installed:

```sh
task_review_repo="$PWD"
task_review_temp=$(mktemp -d /tmp/reckoning-review-XXXXXX)
cp pyproject.toml "$task_review_temp/pyproject.toml"
env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$task_review_temp" \
  PYTHONPATH="$task_review_repo/src" \
  "$task_review_repo/.venv/bin/python" \
  "$task_review_repo/docs/reviews/2026-09-05-review-probes.py"
```

The probe prints the observed failure states as JSON. All probe application state
is created under temporary directories and uses synthetic content.

To reproduce the isolated full-suite run:

```sh
env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$task_review_temp" \
  XDG_CONFIG_HOME="$task_review_temp/config" \
  XDG_STATE_HOME="$task_review_temp/state" \
  PYTHONPATH="$task_review_repo/src" RECKONING_LIVE_SMOKE=0 \
  "$task_review_repo/.venv/bin/python" -c \
  'import os, sys, pytest; os.chdir(os.environ["HOME"]); raise SystemExit(pytest.main([sys.argv[1], "-q", "-p", "no:cacheprovider"]))' \
  "$task_review_repo/tests"
```

These commands reproduce the review's isolation approach. Tests added later may
have different working-directory assumptions and must be inspected before use.
