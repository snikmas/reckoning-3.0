# Phase 7: Record setup supersession decisions

[Back to the overview](overview.md)

## Goal

Make five historical closures truthful under the current setup design without
restoring removed behavior.

## TODO

- [ ] On #67, record that the connector loop and Telegram path remain, while
  #80 and #101 superseded visible unavailable Discord, WhatsApp, and Slack rows.
- [ ] On #71, record that #80 and #89 replaced the explicit add, replace,
  remove, and change-default menu with the configured-state status and focused
  edit hub. Record the recommendation not to restore multi-provider management
  until a current use case requires it.
- [ ] On #76, record that #80 replaced the old automatic continuity-record
  proof with an accepted first conversation plus reconstruction proof. Keep
  truthful incomplete-state handling as the surviving requirement.
- [ ] On #78, record that #101 replaced Rich for setup with the Signal renderer,
  while grouped help and structured doctor output remain current behavior.
- [ ] On #82, record that #80 removed Quick and Custom modes while preserving
  Demo labeling, retry and acceptance, temporary rejected attempts, durable
  accepted history, and reconstruction proof.
- [ ] Use one consistent status vocabulary in every comment: historically
  delivered, still current, superseded, and not restored.
- [ ] Keep all five issues closed. If Mary rejects one recommendation, reopen
  that existing issue and revise its current acceptance contract before code
  changes.

## Files and records

- GitHub issues #67, #71, #76, #78, and #82.
- Superseding issues #80, #89, and #101.

## Data structures

Use a four-column supersession map: old criterion, historical evidence, current
replacement, and disposition.

## Verification

- Static: every comment links both the historical commit evidence and the
  superseding issue.
- Runtime: none. This phase repairs issue history and product-contract meaning.
- Stop condition: do not claim that an old literal criterion passes today when
  the replacement design intentionally removed it.
