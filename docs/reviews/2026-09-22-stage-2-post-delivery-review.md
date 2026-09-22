# Stage 2 post-delivery conversation and evaluation review

Review date: 2026-09-22

Baseline: `4487ae3` on local and live `origin/main` at review start. The review
covers #124, #134-#135, #145-#147, and #137. It does not treat the still-open
Stage 2 tracker or its unfinished journeys as accepted.

## Verification

- Conversation-focused suite: `132 passed`.
- Evaluation and spending suite: `69 passed`.
- Browser: headless Chromium at 1280x720 and 390x844 against a temporary
  fake-provider installation.
- No live provider, paid evaluator, private persona, or private profile was
  used.

The browser run observed these current behaviors:

- A question containing confirmation language produced an ordinary saved turn
  and no decision mutation.
- A proposal page displayed the target and revision. A second tab corrected
  revision 1 to revision 2. Submitting the old confirmation returned a revision
  conflict and left revision 2 proposed. A freshly rendered control confirmed
  revision 2 as revision 3.
- A message containing a literal `script` element was displayed as text and did
  not execute. The 390-pixel layout had no horizontal overflow.
- The first pass found that the fixed composer covered the mobile Inspector at
  maximum scroll and that a long conversation returned above the newest reply.
  The delivery patch adds mobile clearance and redirects a successful message
  to `#latest-reply`. Focused tests pass, and Chromium then showed the Inspector
  clickable and the saved reply fully above the composer.

## Standards review

No hard repository-standard violation was found in the reviewed Stage 2 code.
The code uses typed application operations, durable operation identity,
expected revisions, escaped channel rendering, and separate operational
notices. The mobile visibility defect was a Spec defect, not a style violation,
and is repaired in this delivery.

## Conversation Spec review

| Issue | Current delivery and ancestry | Standards | Spec verdict | Limits |
| --- | --- | --- | --- | --- |
| #124 | `f93aa22` plus repair `5333505`, both on `origin/main`; later consent and replay repairs are on `main` | Pass | Pass after #134/#135 and this rendered mobile repair | Real-language usefulness remains unrun |
| #134 | `0b03c94` plus `35701fe`, merged by `d4925da`, are on `origin/main` | Pass | Pass | Body is stale; current evidence is in the review comment |
| #135 | `0b03c94` plus `35701fe`, merged by `d4925da`, are on `origin/main` | Pass | Pass | Body is stale; current evidence is in the review comment |
| #145 | `4bc7320` repaired by `1d9ea34`, both on `origin/main` | Pass | Pass | Telegram continuation remains owned by open #132 |
| #146 | `4bc7320` repaired by `1d9ea34`, plus this rendered mobile repair | Pass | Pass | Unknown tokenizer uses the documented conservative estimate; live context quality is unrun |
| #147 | `4bc7320` repaired by `1d9ea34`, both on `origin/main` | Pass | Pass within the documented language boundary | Comprehensive Russian, Chinese, and mixed-language danger detection remains explicitly unsupported; live language review is unrun |

The focused evidence covers proposal binding, non-mutating questions, durable
replay, stale forms, session creation and selection, transcript isolation,
contiguous complete-turn history, required-input budgeting, notice separation,
protected-response classification, danger handling, escaping, restart, and
post-save failure recovery.

## #137 live evaluation and spending review

Standards verdict: pass. The implementation does not hide a live run behind
fake mode, does not read private conversation history, and labels token-derived
money as an estimate with a versioned price basis.

Spec verdict: partial.

What passes:

- Live mode requires an explicit route, model, processing permission, and
  positive call limit. A cost-bounded run reserves the configured input and
  output allowance before each attempt.
- Fake mode stays offline. Injected transports exercise the production adapter
  and rendered web driver.
- Message and proposal calls share one call budget. The runtime stops before a
  reservation would exceed the monetary ceiling.
- Missing credentials, missing permission, malformed output, missing usage,
  missing product journeys, and unauthorized runs remain distinguishable.
- JSONL records runtime revision, dirty state, scenario digests, token usage,
  estimated-cost metadata, product results, detector results, and unrun human
  dimensions.
- Fresh fake runs returned nonzero as expected. `early-web` recorded 9 passed
  product cases and 3 missing implementations. `full-stage-2` recorded 9 passed
  product cases, 8 missing product cases, and 3 failed detector cases. All 20
  human-only rubric entries remained `unrun`.

What remains partial:

- The issue body permits a call-limited run with explicit unknown-cost
  acknowledgement, while this TODO says every run requires a positive cost
  limit. The current CLI implements the issue body, not the stricter TODO text.
- JSONL records aggregate token-derived estimated cost. It does not record a
  per-attempt receipt containing call number, reservation, completion status,
  and actual non-negative provider-reported cost.
- The fake-transport tests do not cover a provider-reported monetary amount or
  a reported-cost-over-quote violation. The current provider response contract
  exposes usage tokens, not a quoted and actual charge pair.
- Timeout and retry accounting is enforced by the adapter and shared budget,
  but #137 lacks the acceptance-level JSONL assertions required to prove that
  every failed attempt and reserved spend remains inspectable after a
  mid-suite failure.
- #137 was closed without final acceptance evidence, and its body still says
  not implemented. This review reopened it, applied the `partial` label, and
  added a current evidence comment.

The missing receipt and quote contract needs a product decision. Either retain
the current versioned token-estimate model and revise the TODO wording, or add
a provider-independent per-attempt quote and actual-cost schema. Until that is
decided and implemented, #137 must not be cited as complete spending truth.
Live provider behavior, real monetary acceptance, usefulness, naturalness, and
dated multilingual human judgments remain unrun.
