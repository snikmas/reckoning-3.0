# Shared verification

[Back to the overview](overview.md)

Run focused checks after each phase. Run the full non-live set only after the
focused checks pass.

## Focused static checks

```bash
PYTHONPATH=src python -m pytest -q \
  tests/test_conversation_evaluation.py \
  tests/test_web_decision_journey.py \
  tests/test_main_cli.py
python -m mypy --python-version 3.13 --ignore-missing-imports src
python -m ruff check --select E4,E7,E9,F src tests
python -m compileall -q src tests
git diff --check
```

Report each command separately. A later passing command does not erase an
earlier failure.

## Full non-live suite

```bash
RECKONING_LIVE_SMOKE=0 PYTHONPATH=src python -m pytest -q \
  --ignore=tests/test_live_smoke.py
```

Report passed, failed, skipped, excluded, and unrun counts separately.

## Evaluator runtime checks

Use a fresh output path for each run because evaluator evidence is append-only.

```bash
PYTHONPATH=src python -m reckoning evaluate \
  --scenario-set scenarios/stage2-conversation-v1.json \
  --mode fake \
  --profile early-web \
  --output /tmp/reckoning-stage2-early-web.jsonl

PYTHONPATH=src python -m reckoning evaluate \
  --scenario-set scenarios/stage2-conversation-v1.json \
  --mode fake \
  --profile full-stage-2 \
  --output /tmp/reckoning-stage2-full.jsonl
```

A nonzero exit is expected while a mandatory case is partial, failed, missing,
or unrun. Record product counts, detector counts, excluded scenarios, revision,
dirty state, and limitations.

## Required negative checks

- Change only the expected fixture result. The computed score must not change.
- Supply an unknown profile, duplicate scenario ID, malformed scenario, and
  unsupported schema. Each must fail clearly.
- Remove live authorization. The result must be `unrun`, not fake fallback.
- Exercise a real missing UI operation. The result must be
  `missing-implementation`, not passed.
- Feed deliberately bad output to each detector. The detector test passes only
  when the product record remains failed.

## Browser smoke

Start the local web interface with the fake provider and use the `browse` skill
to check both a normal and a narrow viewport. Submit a question containing the
word `confirm`, ambiguous assent, a correction, and a stale form. Inspect the
visible reply and the resulting durable state after restart.

## Explicitly unrun without separate authorization

- Live or paid model evaluation.
- Private prompts, credentials, or profile data.
- Real Telegram delivery.
- macOS and WSL terminal execution.
- Stage 2 product journeys owned by still-open issues.
