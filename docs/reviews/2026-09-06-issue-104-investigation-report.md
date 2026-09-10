# Independent investigation report for issue #104

Investigation date: 2026-09-06 Asia/Shanghai.

This investigation reproduced the reported behavior with synthetic records,
fake provider transports, and fake Telegram clients. It did not read credentials,
environment files, private profiles, or live application data. It made no live
provider or Telegram calls and changed no production code or existing tests.

## State tested

All finding rows below refer to state `S1`:

- Git HEAD: `7c93c89dee5d6ce38721542dece89f15db788fa8`
- Tracked diff SHA-256 from
  `git diff --binary --no-ext-diff HEAD | sha256sum`:
  `84567487155f89ff84871d11bbbb5a429187aa1112553ba8bf90c3e85a9e5e0a`
- Modified production files: `application.py`, `command.py`, `config.py`,
  `provider_adapters.py`, `providers.py`, `setup_terminal.py`,
  `setup_workflow.py`, `telegram.py`, and `web.py`.
- Modified test files: `test_provider_config.py`, `test_setup_cli.py`,
  `test_setup_terminal.py`, and `test_setup_workflow.py`.
- The source review and source probe were untracked evidence files. Their
  SHA-256 values were `10edc66c...9436` and `955e0cd3...6cc4`.
- The independent probe added during this investigation is
  `docs/reviews/2026-09-05-issue-104-investigation-probes.py`, SHA-256
  `89befaf2...27d7`. It is diagnostic evidence and does not affect imported
  production code.

The original review did not record a diff hash. Its commit and changed-file
inventory match `S1`, but byte-for-byte identity cannot be established. This
matters because that review recorded 381 passed and 11 skipped, while the current
full suite does not finish.

The current full-suite command reached the controlled-terminal arrow-menu test
and blocked in `setup_terminal.py:279`. The first run was interrupted after
`321 passed, 11 skipped in 84.19s`. A second run exited by timeout with code 124.
The suite excluding only
`test_arrow_menu_redraws_in_place_instead_of_stacking` completed with
`380 passed, 11 skipped, 1 deselected in 1.72s`. Issue #103 already tracks setup
prompt and arrow-menu behavior, so this investigation does not create another
setup issue.

## Reproduction

Run the independent probes from the repository root:

```sh
task_review_repo="$PWD"
task_review_temp=$(mktemp -d /tmp/reckoning-issue-104-XXXXXX)
env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$task_review_temp" \
  PYTHONPATH="$task_review_repo/src" \
  "$task_review_repo/.venv/bin/python" \
  "$task_review_repo/docs/reviews/2026-09-05-issue-104-investigation-probes.py"
```

Observed summary:

- two decisions became one persisted decision;
- one concurrent correction disappeared without a conflict;
- a stale instance retrieved and later restored permanently deleted context;
- local-only private context, recent channel history, and a structured reckoning
  input reached a captured request for `https://api.deepseek.com/chat/completions`;
- a web message redirected to Home, where the reply was absent, and created no
  structured continuity file;
- all four current mutation forms accepted a foreign Host, foreign Origin, and
  `Sec-Fetch-Site: cross-site`, then changed state;
- an ambiguous Telegram send caused two processing calls and two remote
  deliveries;
- a provider `RuntimeError` terminated the polling loop;
- negative trial evidence restarted as `accepted=True`;
- an 8-hour difficulty-5 goal became an exact 10-hour estimate and was displaced
  from 9 hours of capacity.

The original source probe also reproduced its asserted failures with the command
documented in issue #104. The independent probe was written separately and adds
continuity conflicts, every current web mutation route, recent channel history,
structured reckoning input, restart behavior, and an ambiguous Telegram send.

## Finding 1: shared instances lose or resurrect records

1. **Disposition:** Confirmed on `S1`.
2. **Exact state:** `S1`.
3. **Reproduction and output:** The independent probe created two applications
   before either wrote. Two decisions produced one stored decision. Two writers
   corrected the same record version; the second write silently removed the first
   correction. A stale personal-context instance retrieved deleted content and an
   unrelated write restored the content while removing its suppression marker.
4. **Root cause and boundary:** `JsonFileModelRunRepository`,
   `JsonFilePersonalContextRepository`, and `JsonFileReckoningRepository` load a
   whole file into memory when constructed. Each save replaces the complete file
   from that private snapshot. `os.replace` prevents a partial JSON document but
   does not serialize read-modify-write operations or refresh readers. Web and
   Gateway construct independent applications against the same installation
   roots. Automation, delegation receipts, personas, trials, watches, briefings,
   and several connector stores use the same cached-snapshot pattern and need a
   follow-up blast-radius check. `JsonFileInterfaceRepository` loads afresh, but
   its separate load and save calls are still not one transaction.
5. **Impact:** Completed model work and cost receipts can disappear. Permanent
   deletion can fail across active instances and deleted private content can
   return. Concurrent corrections lose version order without warning. These are
   privacy, reliability, recovery, and audit failures.
6. **Contract, ADR, and issue overlap:** This breaks permanent-deletion and
   recovery safety gates in the product contract. It overlaps closed issues #37,
   #38, #41, and #59, whose isolated tests did not cover two live instances.
   Per-root transactional storage is compatible with ADR 0002. One database that
   crosses local and personal-server roots would conflict with ADR 0003.
7. **Smallest recommended remedy:** Introduce per-logical-root SQLite storage and
   move one complete record operation into one transaction. SQLite is an embedded
   database, so this does not create a hosted service. Use row-level records,
   suppression markers in the same transaction as deletion, and an expected
   version on correction. Do not put one stale JSON snapshot inside one database
   row. A process lock plus a fresh read for every operation is a possible interim
   patch, but it is easy to bypass and retains full-file rewrites. A single owner
   process would change the current web and Gateway operating model and create a
   new availability dependency.
8. **Regression seam:** Construct two independent applications against one
   temporary installation root. Cover receipt append, current retrieval after
   deletion, unrelated writes after deletion, expected-version correction,
   check-in append, restart, interrupted writes, and backup plus clean restore.
   Add one real multiprocess case after the two-connection contract is green.
9. **Migration, rollback, compatibility:** Migrate one logical root at a time,
   validate counts, identifiers, versions, placement, and suppression markers,
   then switch atomically. Keep a read-only JSON backup for rollback. The transfer
   format currently includes only `*.json`, so SQLite backup must use a consistent
   database snapshot and update restore validation. Never roll back by importing
   a pre-deletion snapshot over newer suppression markers.
10. **Proposed follow-ups:**
    - `Persist model-run receipts transactionally across application instances`,
      high priority, bug, `ready-for-agent`, no blockers. This establishes the
      per-root transactional base and migrates model-run receipts.
    - `Make personal-context deletion authoritative across active instances`,
      critical privacy priority, bug, `ready-for-agent`, blocked by the
      transactional base.
    - `Preserve continuity updates or return an explicit version conflict`, high
      priority, bug, `ready-for-agent`, blocked by the transactional base.
    - `Audit remaining cached JSON repositories for multi-writer loss`, medium
      priority, bug investigation, blocked by the transactional base.
11. **Product decisions for grilling:** Decide whether the first migration covers
    only proven multi-writer paths or every cached durable repository. Engineering
    should still keep databases separate by placement root.

## Finding 2: placement does not constrain the model destination

1. **Disposition:** Confirmed on `S1`.
2. **Exact state:** `S1`.
3. **Reproduction and output:** A fake transport captured the real provider URL
   without making a network call. A private record marked for local processing,
   recent web conversation history, and the raw input to a structured reckoning
   all appeared in requests addressed to
   `https://api.deepseek.com/chat/completions`.
4. **Root cause and boundary:** `load_installation_runtime` derives
   `PlacementState.processing_location` from the node that stores confirmed state.
   Personal-context retrieval compares records with that node label. The provider
   adapter then sends the assembled prompt to its configured endpoint without a
   separate model-destination check. Supplied context has no destination filter.
   Structured reckoning sends its raw input through the same cloud adapter.
   Delegation checks allowed context categories and allowed providers separately,
   but does not prove that a category may be processed at that provider's
   destination.
5. **Impact:** Local-only or otherwise restricted personal context can leave the
   approved processing boundary. Changing providers can broaden disclosure
   without changing the stored category rules. Limited mode can claim full context
   even when the safe action was to omit data or refuse the request.
6. **Contract, ADR, and issue overlap:** This conflicts with ADR 0003 and the
   placement policy in the product contract. It overlaps #38, #58, #60, and setup
   parent #80. Closed issue #38 explicitly required a cloud provider without
   changing authority rules, and #58 required category placement plus truthful
   limited mode. ADR 0006 also requires delegated workers to receive only allowed
   context.
7. **Smallest recommended remedy:** Represent the actual model destination
   separately from storage and application nodes. Before constructing any provider
   request, evaluate each prompt component against a category processing grant for
   that destination. Apply it to personal context, recent history, supplied or
   derived summaries, structured reckoning input, and delegated context. If
   required context is withheld, return accurate limited mode or refuse when a
   partial answer would mislead. Reject filtering only newly retrieved personal
   context and reject treating provider selection as blanket consent.
8. **Regression seam:** Send web and Telegram messages through the installed
   application boundary and inspect a fake provider transport. Parameterize local
   and cloud destinations, provider changes, all context routes, approved and
   denied categories, limited mode, refusal, and delegated work.
9. **Migration, rollback, compatibility:** Existing node-placement values do not
   state whether a third-party provider may process a category. Migrate them to an
   explicit unclassified or denied remote-processing state and ask for confirmation
   before enabling cloud disclosure. Setup must display the destination and the
   categories included. Rollback cannot convert an explicit denial into the old
   implicit allow behavior.
10. **Proposed follow-up:** `Enforce category processing grants at the actual model
    destination`, critical privacy priority, bug. Use `needs-info` until the product
    owner decides the consent contract, then `ready-for-agent`. No code blocker.
11. **Product decisions for grilling:** Decide whether selecting a cloud provider
    authorizes only the current message, which categories may accompany it, how a
    provider change renews consent, and when missing context requires refusal
    instead of limited mode.

## Finding 3: ordinary channels do not complete the core continuity loop

1. **Disposition:** Confirmed on `S1`. This is a completeness failure, not a
   regression from a previously connected channel journey.
2. **Exact state:** `S1`.
3. **Reproduction and output:** Posting an ordinary web message returned
   `303 See Other` with `Location: /`. Home loaded and did not show the reply;
   `/simon` did show it. Two conversation messages were durable in interface
   state, no structured continuity file existed, and Review still rendered fixed
   explanatory text.
4. **Root cause and boundary:** `ApplicationChannelResponder` calls only
   `respond_with_channel_context`. No web, Telegram, or interface code calls
   `start_reckoning`, correction, confirmation, `resume_decision`, or
   `record_check_in`. Home reads summary strings from interface state rather than
   the continuity repository. Review is a placeholder. The separate navigation
   defect is a literal successful redirect to `/`, which becomes Home after the
   first message marks the user as returning.
5. **Impact:** The shipped interfaces support conversation but not Reckoning's
   defining product behavior. Decisions, reasons, corrections, and outcomes do
   not survive through ordinary use. A successful web submission also hides its
   immediate response.
6. **Contract, ADR, and issue overlap:** This reopens integration claims from
   closed issues #35, #36, #37, #55, and #57. Their service-level capabilities
   exist, but no channel journey assembles them. The product contract requires the
   core continuity loop and a command center that reads real confirmed state.
7. **Smallest recommended remedy:** First fix the redirect alone. Separately add
   an explicit channel interaction contract in which the model may propose a
   reckoning but deterministic application operations own correction,
   confirmation, identifiers, and persistence. Home and Review must project the
   same durable decisions and check-ins. Reject automatic intent classification
   that silently confirms casual conversation. Do not prove the feature by calling
   domain services directly halfway through a channel test.
8. **Regression seam:** Through the WSGI boundary, complete proposal, correction,
   confirmation, application reconstruction, resumption, check-in, and later use
   of that outcome. Assert that a casual message creates only conversation. Repeat
   the interaction contract through a fake Telegram client after its delivery
   state is durable.
9. **Migration, rollback, compatibility:** Preserve existing channel sessions as
   conversation history. Import existing continuity identifiers unchanged. New
   interaction state must resume after restart and remain readable if the UI is
   rolled back. Do not copy durable decisions into interface summary strings.
10. **Proposed follow-ups:**
    - `Return successful web messages to the visible Simon reply`, medium priority,
      bug, `ready-for-agent`, no blockers.
    - `Complete the core continuity loop through the web channel`, high product
      priority, enhancement. Use `needs-info` until the interaction contract is
      chosen. Block it on continuity version safety, model-destination enforcement,
      web mutation protection, and the redirect fix.
    - `Complete the core continuity loop through Telegram`, high product priority,
      enhancement, blocked by the web interaction contract and durable Telegram
      processing.
11. **Product decisions for grilling:** Choose the explicit interaction language
    or controls for propose, correct, confirm, resume, and check-in. Decide what
    can trigger a proposal, what must never happen from casual text, and whether
    web defines the first interaction contract before Telegram adopts it.

## Finding 4: Telegram retries repeat work and provider failures stop polling

1. **Disposition:** Confirmed on `S1`.
2. **Exact state:** `S1`.
3. **Reproduction and output:** The source probe processed update IDs
   `1, 2, 1, 2` after one delivery failure. The independent ambiguous-send probe
   processed update 7 twice and recorded two remote deliveries of the same reply.
   A synthetic provider `RuntimeError` escaped `run_forever` and terminated it.
4. **Root cause and boundary:** `run_once` handles an update and calls the model
   before sending. It returns its new offset only after the entire batch completes.
   There is no durable inbox keyed by update ID and no outbox containing a completed
   response. The runtime offset remains an in-memory local variable. `run_forever`
   catches `TelegramBotApiError` but not the provider failure wrapped as
   `RuntimeError` by the application boundary.
5. **Impact:** Delivery failure can repeat paid model work, append duplicate
   conversation state, and send duplicate replies. A provider outage kills the
   Gateway, so later updates wait until an operator restarts it. An ambiguous send
   can be reported more strongly than the remote evidence permits.
6. **Contract, ADR, and issue overlap:** This violates the reliable execution and
   truthful receipt rules in the product contract. It overlaps closed issues #49,
   #57, and #59. Telegram's Bot API documents `update_id` for deduplication and
   says an update becomes confirmed when `getUpdates` receives a higher offset.
   `sendMessage` returns a Message on known success but exposes no Bot API
   idempotency key.
7. **Smallest recommended remedy:** Store accepted input, completed processing,
   pending delivery, confirmed delivery, provider failure, and delivery-unknown as
   distinct durable states. Key the inbox by Telegram update ID. Store the
   generated reply in an outbox before sending and reuse it on retry. Catch expected
   provider failures per update, record them, and continue polling. Do not catch
   every programming error. The strongest truthful contract is exactly-once local
   processing plus at-least-once remote delivery with an explicit unknown state
   after an ambiguous send. The Bot API cannot guarantee exactly-once remote
   delivery here.
8. **Regression seam:** Exercise the polling application with a fake client and a
   temporary durable store. Cover partial batches, duplicate updates, restart after
   processing and before delivery, provider outage, known send failure, ambiguous
   send, and successful replay without a second model call.
9. **Migration, rollback, compatibility:** Initialize the inbox from the current
   configured offset without marking unseen work complete. Preserve pending outbox
   entries across restart. A rollback must not discard pending or unknown
   deliveries. If SQLite is adopted, back up the inbox and outbox consistently with
   other root state.
10. **Proposed follow-up:** `Make Telegram processing and delivery recoverable`,
    high reliability and cost priority, bug. Use `needs-info` until the ambiguous
    delivery retry policy is chosen, then `ready-for-agent`. Block it on the
    transactional storage base and model-destination enforcement.
11. **Product decisions for grilling:** After an ambiguous send, choose automatic
    retry with a visible duplicate risk, operator confirmation before retry, or a
    delivery-unknown notice with no automatic retry.

## Finding 5: the web application accepts foreign-origin mutations

1. **Disposition:** Confirmed at the WSGI boundary on `S1`. A browser exploit was
   not needed to confirm the missing server checks and was not attempted.
2. **Exact state:** `S1`.
3. **Reproduction and output:** Requests with `Host: foreign.invalid:8000`,
   `Origin: https://foreign.invalid`, and `Sec-Fetch-Site: cross-site` returned
   `303 See Other` and changed state for `/messages` and profile correction,
   confirmation, and rejection. The rendered message form had no CSRF field.
4. **Root cause and boundary:** The WSGI adapter dispatches POST mutations without
   validating Host, Origin, Fetch Metadata, or a CSRF token. Loopback bind
   validation happens at server startup, not per request. The existing private
   administration check applies only to GET requests under `/control` and does not
   protect conversation or personal-context mutations.
5. **Impact:** A page from another site can attempt to make the user's local
   Reckoning process change conversation or personal-context state. Browser and
   private-network rules can affect exploitability, but the server currently
   accepts the forged request and provides no independent mutation boundary.
6. **Contract, ADR, and issue overlap:** This violates the product contract's
   private-administration and permission safety gates. It overlaps #55 and #58 but
   neither closed issue covered cross-site mutation at the WSGI boundary.
7. **Smallest recommended remedy:** Put one policy before all non-safe methods.
   Allow only configured loopback Host values, validate Origin with a documented
   fallback for clients that omit it, require a browser-session CSRF token, and
   reject `Sec-Fetch-Site: cross-site` as defense in depth. Apply the policy before
   reading the body or mutating state. Keep localhost and supported SSH-forward
   origins usable. CORS, loopback binding, SameSite cookies, or Fetch Metadata
   alone are not sufficient replacements for the server-side policy.
8. **Regression seam:** Parameterize every mutation route at the WSGI boundary for
   foreign Host, foreign Origin, missing or invalid token, cross-site Fetch
   Metadata, legitimate loopback, and a supported SSH tunnel. Add one browser test
   for the actual cross-site request behavior after the server policy passes.
9. **Migration, rollback, compatibility:** Existing pages need a session cookie
   and token in every form. A server restart may invalidate sessions cleanly.
   Host matching must handle ports, IPv4, IPv6 loopback, `localhost`, and the local
   endpoint used by an SSH forward without trusting forwarded headers by default.
10. **Proposed follow-up:** `Protect every local web mutation before dispatch`,
    critical security priority, bug, `ready-for-agent`, no blockers. It must land
    before new continuity mutation routes.
11. **Product decisions for grilling:** Confirm the supported browser origins and
    SSH-tunnel form. The protection mechanism itself is an engineering safety
    requirement, not an optional preference.

## Finding 6: evidence completeness is reported as trial acceptance

1. **Disposition:** Confirmed on `S1`. Current impact is trial reporting, not the
   separate release-readiness calculation.
2. **Exact state:** `S1`.
3. **Reproduction and output:** The probe stored one failed automated check and a
   negative value for every real-use metric. After repository restart,
   `missing_evidence` was empty and `accepted` was true. The stored schema has no
   acceptance or rejection field.
4. **Root cause and boundary:** `SliceTrial.accepted` returns only
   `not self.missing_evidence`. Evidence values are free-form strings and have no
   structured outcome. Repository-wide search found no production reader of
   `SliceTrial.accepted`; `release_readiness` independently checks booleans in
   `release-evidence.json`.
5. **Impact:** Trial reports can state acceptance after failed checks and Mary's
   explicit rejection. The flag does not currently make the CLI declare release
   readiness, but it can mislead a maintainer or become an unsafe gate when the
   trial model is connected later.
6. **Contract, ADR, and issue overlap:** This contradicts the product contract's
   explicit first-user judgment and product-hypothesis rules. It reopens closed
   issue #39. It does not currently overlap the separate release gate implemented
   under #59 except in naming and future integration.
7. **Smallest recommended remedy:** Rename the current concept to evidence
   completeness. Store structured automated-check outcomes and an explicit
   `pending`, `accepted`, or `rejected` decision with reason and date. Acceptance
   requires complete required evidence, passing required safety checks, and Mary's
   explicit accepted decision. Reject keyword parsing and reject grandfathering
   old records as accepted because all text fields exist.
8. **Regression seam:** Use the public trial recorder and JSON repository across a
   restart. Cover complete negative evidence, failed required checks, explicit
   rejection, positive evidence without a decision, explicit acceptance, and the
   separately invoked release-readiness function.
9. **Migration, rollback, compatibility:** Migrate schema-1 trials to evidence
   complete or incomplete plus decision `pending`. Preserve every original value.
   Do not infer acceptance. A rollback must retain the explicit decision in a
   compatible extension or refuse rather than silently convert rejection to true.
10. **Proposed follow-up:** `Separate trial evidence completeness from explicit
    acceptance`, medium product-integrity priority, bug. Use `needs-info` until the
    material-change and release relationship is decided, then `ready-for-agent`.
    No code blocker.
11. **Product decisions for grilling:** Decide which failed safety checks make
    acceptance impossible, what material change returns an accepted trial to
    review, and whether explicit trial acceptance supplies one release-evidence
    input or remains reporting only.

## Finding 7: the planning coefficient implies unsupported precision

1. **Disposition:** Partly confirmed on `S1`.
2. **Exact state:** `S1`.
3. **Reproduction and output:** The formula changed an 8-hour difficulty-5 goal
   into exactly 10.0 hours and displaced it from 9 hours of capacity. Repository
   search found the coefficient only in `planning.py` and found no production call
   to `calculate_feasible_set`; the only caller is a test.
4. **Root cause and boundary:** `GoalPlanningService.calculate_feasible_set`
   multiplies user-entered weekly effort by `0.75 + difficulty * 0.1` and rounds to
   two decimals. No calibration data, assumption record, or uncertainty range
   supports that relationship. The service reason claims difficulty and prior
   outcomes were applied, but the exact adjustment is opaque.
5. **Impact:** The coefficient can change inclusion in a feasible goal set and can
   later present false precision. It does not currently affect a shipped web, CLI,
   or provider response because production code does not call the method.
6. **Contract, ADR, and issue overlap:** It overlaps closed issues #43 and #46 and
   conflicts with ADR 0008 if exposed as evidence-backed precision. ADR 0008
   requires assumptions, ranges, uncertainty, and observed outcomes.
7. **Smallest recommended remedy:** Before exposing this service, either use the
   user's raw estimate with an explicit uncertainty range or represent the
   difficulty adjustment as a named, visible uncalibrated assumption. Collect
   outcome evidence before fitting a coefficient. Reject inventing a replacement
   number and reject keeping two-decimal output with only a disclaimer.
8. **Regression seam:** Keep arithmetic tests, but add a product-level planning
   test that inspects the displayed inputs, assumption, range, and uncertainty.
   Calibration needs observed forecast versus outcome data; a formula unit test
   cannot validate usefulness.
9. **Migration, rollback, compatibility:** No stored feasible-set result or
   production caller was found, so no current data migration is required. Preserve
   goal inputs unchanged if the result model gains ranges or assumption metadata.
10. **Proposed follow-up:** `Expose feasible-goal estimates only as assumptions and
    ranges`, low current priority, enhancement, `needs-triage`, no blockers. It
    should land before a production caller or planning UI uses the method.
11. **Product decisions for grilling:** Decide whether the first planning path uses
    raw user estimates or a visibly uncalibrated difficulty range, and what amount
    of real outcome evidence is enough to attempt calibration.

## Proposed dependency graph

```text
T1  Transactional model-run base
├── T2  Authoritative personal-context deletion
├── T3  Continuity version conflicts
├── T4  Remaining cached-JSON blast-radius audit
└── T8  Durable Telegram inbox and outbox

T4a Model-destination grants ─┬── T7  Web core continuity journey
T5  Simon redirect ──────────┤
T6  Web mutation protection ─┤
T3  Continuity conflicts ────┘

T7 + T8 ── T9  Telegram core continuity journey

T10 Trial acceptance      independent after product decisions
T11 Planning assumptions  independent and lower priority
```

`T4a` names the model-destination ticket to avoid confusing it with the cached
JSON audit shown as `T4` in this compact graph.

The implementation frontier after product-owner review is T1, T5, T6, and any
independent product decisions needed for model-destination grants, Telegram
delivery uncertainty, trial acceptance, and the channel interaction contract.
No follow-up issue has been created by this investigation.

## Product decisions that need grilling

1. Cloud processing consent by context category and provider change.
2. The explicit channel interaction contract for proposal, correction,
   confirmation, resumption, and check-in.
3. The policy after an ambiguous Telegram send.
4. Trial acceptance, material-change review, and its relationship to release
   readiness.
5. Raw estimates versus visible uncalibrated ranges before planning has outcome
   evidence.
6. Whether the first persistence migration covers only reproduced stores or all
   cached durable repositories with the same multi-writer risk.

## External protocol and security references

- Telegram Bot API: https://core.telegram.org/bots/api
- OWASP CSRF Prevention Cheat Sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
- MDN Fetch Metadata guide:
  https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Fetch_metadata

## Investigation status

`DONE_WITH_CONCERNS`. All seven concerns have a current disposition, causal layer,
test seam, and proposed issue boundary. No production fix was authorized or made.
The concern is the current full-suite hang already covered by #103 and the lack of
an original diff hash, which prevents a byte-identical comparison with the source
review state.
