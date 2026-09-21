# Evaluate conversation quality

Use `reckoning evaluate` to run the versioned conversation scenarios through
the ordinary application interface. Fake mode is deterministic, makes no
network request, and uses no provider credentials.

## Run the early Web profile

From the repository root, run:

```bash
PYTHONPATH=src python -m reckoning evaluate \
  --scenario-set scenarios/stage2-conversation-v1.json \
  --mode fake \
  --profile early-web \
  --output /tmp/reckoning-stage2-early-web.jsonl
```

Use a new output path for each independent run. The evaluator appends one JSON
record per scenario and includes earlier records from the same file in its
summary.

The command returns 0 only when every selected record in the output passes. It
returns 1 when any selected record is partial, failed, missing implementation,
or unrun. A nonzero result is expected while a required product journey or
human judgment remains incomplete.

## Run the full Stage 2 profile

To include every Stage 2 scenario, run:

```bash
PYTHONPATH=src python -m reckoning evaluate \
  --scenario-set scenarios/stage2-conversation-v1.json \
  --mode fake \
  --profile full-stage-2 \
  --output /tmp/reckoning-stage2-full.jsonl
```

`early-web` selects the scenarios tagged for the current Web journey.
`full-stage-2` selects the complete scenario set. The console summary names
scenarios excluded by the selected profile.

## Read the evidence

Each JSONL record identifies the run, scenario, profile, execution kind,
provider route, result, and limitations. It also records:

- the Git revision and whether the working tree was dirty;
- SHA-256 digests for the scenario set and the individual scenario;
- observed output and application-state evidence;
- rubric results, latency, and cost status.

The console summary separates product counts from detector counts. Product
counts describe the application behavior under test. Detector counts describe
deliberately good or bad fixtures that check whether an evaluator rule can
distinguish known cases. A passing detector does not make a failing product
scenario pass.

Fake mode cannot prove naturalness, user usefulness, live-provider quality,
provider cost, or Stage 2 acceptance. Human-judgment dimensions remain
`unrun`, and missing product journeys remain visible rather than falling back
to a fake pass.

## Authorize a live run separately

Live evaluation can send scenario content to a provider and may cost money. Do
not infer permission from an installed provider. Obtain explicit processing
authorization and a current budget before running it.

A live run requires `--mode live`, a provider or route, a concrete model,
`--allow-processing`, and a positive `--max-calls` limit. It also requires
either an enforceable `--max-cost` limit or
`--acknowledge-unknown-cost`. Review `reckoning evaluate --help` for the exact
flags. If authorization or budget data is incomplete, the evaluator records
the live scenarios as `unrun` and does not silently use fake mode.
