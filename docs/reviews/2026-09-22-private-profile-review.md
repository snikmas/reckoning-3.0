# Private profile and authority review for #63

Review date: 2026-09-22

Baseline: `4487ae3` on local and live `origin/main` at review start.

## Evidence

Issue #63 cites historical implementation `bef9040`. Its current-history
replacement is `340f2de`, which is an ancestor of `origin/main`. The focused
suite passed with `36 passed`:

```text
tests/test_user_profile_onboarding.py
tests/test_personal_context_persistence.py
tests/test_multilingual_personal_context.py
tests/test_context_lifecycle_and_maintenance.py
tests/test_recovery_contract.py
```

The tests use fictional profile text. No private profile source or credential
was read.

## Standards verdict

Pass. Profile import is routed through explicit placement policy, stores
reviewable records with provenance, does not retain the imported raw file, and
keeps profile text below protected instructions. Persistence and lifecycle
changes use the current transactional personal-context authority.

## Spec verdict

Pass on deterministic evidence.

- Setup accepts optional Markdown import and a no-profile path.
- Local, personal-server, and hybrid placement choose the private storage root.
- Profile text remains user data. It cannot change protected instructions,
  permissions, tools, or application rules.
- Deterministic parsing creates separate unconfirmed records with provenance.
- The first conversation selects relevant bootstrap context and marks it
  unconfirmed.
- Rendered controls support correction, confirmation, rejection, and skip.
- Confirmed context survives restart, backup, restore, and migration.
- Rejected and permanently deleted context does not return. Correction does not
  silently reactivate archived or forgotten records.
- Later retrieval applies current-version, permission, freshness, sensitivity,
  source, and placement checks.
- Skipping remains visible as limited context rather than fabricated knowledge.

## Limits

This review does not expose or assess Mary's private profile. It does not claim
that the broader lifecycle service has a complete user-facing archive, forget,
recover, and delete journey. That separate product classification remains
partial under #40-#42.
