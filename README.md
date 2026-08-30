# Reckoning

Reckoning currently provides a small local web interface for sending a message
through its application boundary. The repository also contains the first
continuity slice: a reckoning can be proposed, corrected, confirmed, explained,
resumed after restart, and linked to a dated outcome check-in.

The default local model is a deterministic fake. The same application boundary
also supports DeepSeek. Recent conversation messages remain in memory. Confirmed
continuity records use an atomic JSON file so that they survive a restart.

## Run locally

Reckoning requires Python 3.13 and has no runtime dependencies.

```bash
PYTHONPATH=src python3 -m reckoning
```

Open `http://127.0.0.1:8000`. Stop the server with `Ctrl+C`.

To run with DeepSeek, provide the key through the environment and select the
provider. Reckoning does not store the key.

```bash
DEEPSEEK_API_KEY=... PYTHONPATH=src python3 -m reckoning --provider deepseek
```

Each attempted provider run records the provider, model calls, latency, retries,
token usage, and failure status in the application run repository. Token usage is
the billable unit; Reckoning does not hard-code provider prices.

The local application stores continuity data in
`~/.local/state/reckoning/continuity.json`. The file contains plain JSON and is
not encrypted by Reckoning. The current web page exposes conversation only;
the continuity operations are available through `ReckoningApplication` while
the interface remains under development.

## Run tests

Install pytest 9 in a development environment, then run:

```bash
PYTHONPATH=src python3 -m pytest
```

The tests use fake time, model, placement, connectors, and temporary storage.
They do not call an external model or service.

## Implemented domain services

The Python API now includes services for private-slice trials, multilingual
personal context, context lifecycle and maintenance, directions and goals,
planning horizons, bounded research, goal forecasts, and forecast revisions.
These services keep proposed and confirmed state separate where user acceptance
matters. The local web page still exposes conversation only.
